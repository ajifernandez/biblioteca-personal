from datetime import datetime

from flask import Blueprint, Response, jsonify, redirect, render_template, request, url_for

from ..db import get_db
from ..services import (
    find_books,
    lookup_isbn_external,
    save_cover_upload,
)

books = Blueprint("books", __name__)


@books.route("/export.csv")
def export_csv():
    db = get_db()
    rows = db.execute("SELECT * FROM books ORDER BY id").fetchall()
    import io, csv

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[
        "id",
        "isbn",
        "title",
        "author",
        "publisher",
        "published_year",
        "location",
        "cover_url",
        "description",
    ])
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row[field] for field in writer.fieldnames})
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=biblioteca.csv"},
    )


@books.route("/import-csv", methods=["POST"])
def import_csv():
    import io, csv

    file = request.files.get("csv_file")
    if not file or not file.filename:
        return redirect(url_for("pages.csv_page", error="Selecciona un archivo CSV"))

    try:
        stream = io.StringIO(file.stream.read().decode("utf-8-sig"))
        reader = csv.DictReader(stream)
    except (UnicodeDecodeError, csv.Error):
        return redirect(url_for("pages.csv_page", error="No se pudo leer el archivo CSV"))

    db = get_db()
    inserted = 0
    updated = 0
    skipped = 0
    for row in reader:
        title = (row.get("title") or "").strip()
        if not title:
            skipped += 1
            continue

        values = (
            (row.get("isbn") or "").strip() or None,
            title,
            (row.get("author") or "").strip(),
            (row.get("publisher") or "").strip(),
            (row.get("published_year") or "").strip(),
            (row.get("location") or "").strip(),
            (row.get("cover_url") or "").strip(),
            (row.get("description") or "").strip(),
        )
        book_id = (row.get("id") or "").strip()
        existing = (
            db.execute("SELECT id FROM books WHERE id = ?", (book_id,)).fetchone()
            if book_id
            else None
        )
        if existing:
            db.execute(
                """UPDATE books SET isbn=?, title=?, author=?, publisher=?,
                       published_year=?, location=?, cover_url=?, description=?
                   WHERE id=?""",
                (*values, book_id),
            )
            updated += 1
        else:
            db.execute(
                """INSERT INTO books
                       (isbn, title, author, publisher, published_year, location, cover_url, description)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                values,
            )
            inserted += 1
    db.commit()
    return redirect(
        url_for("pages.csv_page", imported=1, inserted=inserted, updated=updated, skipped=skipped)
    )


@books.route("/books", methods=["POST"])
def create_book():
    db = get_db()
    isbn = request.form.get("isbn", "").strip() or None
    title = request.form.get("title", "").strip()
    if not title:
        return "El título es obligatorio", 400

    cover_url = save_cover_upload(request.files.get("cover_file")) or request.form.get(
        "cover_url", ""
    ).strip()

    cur = db.execute(
        """INSERT INTO books
           (isbn, title, author, publisher, published_year, cover_url, description, location)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           RETURNING id""",
        (
            isbn,
            title,
            request.form.get("author", "").strip(),
            request.form.get("publisher", "").strip(),
            request.form.get("published_year", "").strip(),
            cover_url,
            request.form.get("description", "").strip(),
            request.form.get("location", "").strip(),
        ),
    )
    book_id = cur.fetchone()["id"]
    db.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True, "book_id": book_id})
    return redirect(url_for("books.book_detail", book_id=book_id))


@books.route("/books/<int:book_id>")
def book_detail(book_id):
    db = get_db()
    book = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        return "Libro no encontrado", 404
    current_loan = db.execute(
        "SELECT * FROM loans WHERE book_id = ? AND returned_at IS NULL ORDER BY loaned_at DESC LIMIT 1",
        (book_id,),
    ).fetchone()
    loans = db.execute(
        "SELECT * FROM loans WHERE book_id = ? ORDER BY loaned_at DESC, id DESC",
        (book_id,),
    ).fetchall()
    current_reading = db.execute(
        "SELECT * FROM reading_entries WHERE book_id = ? AND finished_at IS NULL ORDER BY started_at DESC LIMIT 1",
        (book_id,),
    ).fetchone()
    reading_entries = db.execute(
        "SELECT * FROM reading_entries WHERE book_id = ? ORDER BY started_at DESC, id DESC",
        (book_id,),
    ).fetchall()
    copies = (
        db.execute(
            "SELECT id, location FROM books WHERE isbn = ? ORDER BY added_at",
            (book["isbn"],),
        ).fetchall()
        if book["isbn"]
        else []
    )
    return render_template(
        "book_detail.html",
        book=book,
        current_loan=current_loan,
        loans=loans,
        current_reading=current_reading,
        reading_entries=reading_entries,
        copies=copies,
    )


@books.route("/books/<int:book_id>", methods=["POST"])
def update_book(book_id):
    db = get_db()
    existing = db.execute(
        "SELECT cover_url FROM books WHERE id = ?", (book_id,)
    ).fetchone()
    if not existing:
        return "Libro no encontrado", 404

    cover_url = (
        save_cover_upload(request.files.get("cover_file"))
        or request.form.get("cover_url", "").strip()
        or existing["cover_url"]
    )
    isbn = request.form.get("isbn", "").strip() or None

    db.execute(
        """UPDATE books SET
           isbn=?, title=?, author=?, publisher=?, published_year=?,
           location=?, cover_url=?, description=?
           WHERE id=?""",
        (
            isbn,
            request.form.get("title", "").strip(),
            request.form.get("author", "").strip(),
            request.form.get("publisher", "").strip(),
            request.form.get("published_year", "").strip(),
            request.form.get("location", "").strip(),
            cover_url,
            request.form.get("description", "").strip(),
            book_id,
        ),
    )
    db.commit()
    return redirect(url_for("books.book_detail", book_id=book_id))


@books.route("/books/<int:book_id>/delete", methods=["POST"])
def delete_book(book_id):
    db = get_db()
    db.execute("DELETE FROM books WHERE id = ?", (book_id,))
    db.commit()
    return redirect(url_for("pages.index"))


@books.route("/books/bulk-location", methods=["POST"])
def bulk_update_location():
    db = get_db()
    book_ids = [b for b in request.form.getlist("book_ids") if b.isdigit()]
    location = request.form.get("location", "").strip()
    if book_ids:
        placeholders = ",".join("?" for _ in book_ids)
        db.execute(
            f"UPDATE books SET location = ? WHERE id IN ({placeholders})",
            [location, *book_ids],
        )
        db.commit()

    q = request.form.get("q", "").strip()
    field = request.form.get("field", "todo")
    view = request.form.get("view", "grid")
    return render_template("partials/_book_list.html", books=find_books(q, field), view=view)


@books.route("/books/bulk-loan", methods=["POST"])
def bulk_create_loan():
    db = get_db()
    book_ids = [b for b in request.form.getlist("book_ids") if b.isdigit()]
    borrower_name = request.form.get("borrower_name", "").strip()
    if book_ids and borrower_name:
        already_loaned = {
            row["book_id"]
            for row in db.execute(
                "SELECT book_id FROM loans WHERE returned_at IS NULL"
            ).fetchall()
        }
        loaned_at = datetime.now().strftime("%Y-%m-%d")
        to_loan = [b for b in book_ids if int(b) not in already_loaned]
        db.executemany(
            "INSERT INTO loans (book_id, borrower_name, loaned_at) VALUES (?, ?, ?)",
            [(book_id, borrower_name, loaned_at) for book_id in to_loan],
        )
        db.commit()

    q = request.form.get("q", "").strip()
    field = request.form.get("field", "todo")
    view = request.form.get("view", "grid")
    return render_template("partials/_book_list.html", books=find_books(q, field), view=view)


@books.route("/books/<int:book_id>/refill", methods=["POST"])
def refill_book(book_id):
    db = get_db()
    book = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        return jsonify({"applied": False, "reason": "not_found"}), 404
    if not book["isbn"]:
        return jsonify({"applied": False, "reason": "no_isbn"})

    info = lookup_isbn_external(book["isbn"])
    if not info:
        return jsonify({"applied": False, "reason": "no_match"})

    db.execute(
        """UPDATE books SET
           title=?, author=?, publisher=?, published_year=?, cover_url=?, description=?
           WHERE id=?""",
        (
            info["title"] or book["title"],
            info["author"] or book["author"],
            info["publisher"] or book["publisher"],
            info["published_year"] or book["published_year"],
            info["cover_url"] or book["cover_url"],
            info["description"] or book["description"],
            book_id,
        ),
    )
    db.commit()
    return jsonify({"applied": True, "source": info["source"]})


@books.route("/books/<int:book_id>/loans", methods=["POST"])
def create_loan(book_id):
    db = get_db()
    book = db.execute("SELECT id FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        return "Libro no encontrado", 404

    current_loan = db.execute(
        "SELECT id FROM loans WHERE book_id = ? AND returned_at IS NULL LIMIT 1",
        (book_id,),
    ).fetchone()
    if current_loan:
        return "Este libro ya está prestado", 400

    borrower_name = request.form.get("borrower_name", "").strip()
    if not borrower_name:
        return "El nombre de la persona es obligatorio", 400

    loaned_at = request.form.get("loaned_at", "").strip() or datetime.now().strftime(
        "%Y-%m-%d"
    )
    notes = request.form.get("notes", "").strip()
    db.execute(
        """INSERT INTO loans (book_id, borrower_name, loaned_at, notes)
           VALUES (?, ?, ?, ?)""",
        (book_id, borrower_name, loaned_at, notes),
    )
    db.commit()
    return render_template("partials/_loans.html", book=db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone(), current_loan=db.execute("SELECT * FROM loans WHERE book_id = ? AND returned_at IS NULL ORDER BY loaned_at DESC LIMIT 1", (book_id,)).fetchone(), loans=db.execute("SELECT * FROM loans WHERE book_id = ? ORDER BY loaned_at DESC, id DESC", (book_id,)).fetchall())


@books.route("/books/<int:book_id>/loans/<int:loan_id>/return", methods=["POST"])
def return_loan(book_id, loan_id):
    db = get_db()
    loan = db.execute(
        "SELECT * FROM loans WHERE id = ? AND book_id = ?",
        (loan_id, book_id),
    ).fetchone()
    if not loan:
        return "Préstamo no encontrado", 404

    returned_at = request.form.get("returned_at", "").strip() or datetime.now().strftime(
        "%Y-%m-%d"
    )
    return_notes = request.form.get("return_notes", "").strip()
    db.execute(
        "UPDATE loans SET returned_at = ?, return_notes = ? WHERE id = ? AND book_id = ?",
        (returned_at, return_notes, loan_id, book_id),
    )
    db.commit()
    return render_template("partials/_loans.html", book=db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone(), current_loan=db.execute("SELECT * FROM loans WHERE book_id = ? AND returned_at IS NULL ORDER BY loaned_at DESC LIMIT 1", (book_id,)).fetchone(), loans=db.execute("SELECT * FROM loans WHERE book_id = ? ORDER BY loaned_at DESC, id DESC", (book_id,)).fetchall())


# Lecturas (si se mantienen en UI)
@books.route("/books/<int:book_id>/reading", methods=["POST"])
def create_reading(book_id):
    db = get_db()
    book = db.execute("SELECT id FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        return "Libro no encontrado", 404

    current_reading = db.execute(
        "SELECT id FROM reading_entries WHERE book_id = ? AND finished_at IS NULL LIMIT 1",
        (book_id,),
    ).fetchone()
    if current_reading:
        return "Ya estás leyendo este libro", 400

    started_at = request.form.get("started_at", "").strip() or datetime.now().strftime(
        "%Y-%m-%d"
    )
    db.execute(
        "INSERT INTO reading_entries (book_id, started_at) VALUES (?, ?)",
        (book_id, started_at),
    )
    db.commit()
    return render_template("partials/_reading.html", book=db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone(), current_reading=db.execute("SELECT * FROM reading_entries WHERE book_id = ? AND finished_at IS NULL ORDER BY started_at DESC LIMIT 1", (book_id,)).fetchone(), reading_entries=db.execute("SELECT * FROM reading_entries WHERE book_id = ? ORDER BY started_at DESC, id DESC", (book_id,)).fetchall())


@books.route("/books/<int:book_id>/reading/<int:entry_id>/finish", methods=["POST"])
def finish_reading(book_id, entry_id):
    db = get_db()
    entry = db.execute(
        "SELECT * FROM reading_entries WHERE id = ? AND book_id = ?",
        (entry_id, book_id),
    ).fetchone()
    if not entry:
        return "Lectura no encontrada", 404

    finished_at = request.form.get("finished_at", "").strip() or datetime.now().strftime(
        "%Y-%m-%d"
    )
    rating = request.form.get("rating", type=int)
    if rating is not None and not 1 <= rating <= 5:
        rating = None
    notes = request.form.get("notes", "").strip()
    db.execute(
        "UPDATE reading_entries SET finished_at = ?, rating = ?, notes = ? WHERE id = ? AND book_id = ?",
        (finished_at, rating, notes, entry_id, book_id),
    )
    db.commit()
    return render_template("partials/_reading.html", book=db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone(), current_reading=db.execute("SELECT * FROM reading_entries WHERE book_id = ? AND finished_at IS NULL ORDER BY started_at DESC LIMIT 1", (book_id,)).fetchone(), reading_entries=db.execute("SELECT * FROM reading_entries WHERE book_id = ? ORDER BY started_at DESC, id DESC", (book_id,)).fetchall())
