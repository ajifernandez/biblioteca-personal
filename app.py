import os
import sqlite3
import uuid
from datetime import datetime
from urllib.parse import quote_plus

import requests
from flask import Flask, g, jsonify, redirect, render_template, request, send_from_directory, url_for

DB_PATH = os.environ.get("DB_PATH", "/data/biblioteca.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")
VERSION_PATH = os.path.join(os.path.dirname(__file__), "VERSION")
UPLOAD_DIR = os.path.join(os.path.dirname(DB_PATH), "uploads")
ALLOWED_COVER_EXT = {"jpg", "jpeg", "png", "webp", "gif"}


def load_app_version():
    env_version = os.environ.get("APP_VERSION")
    if env_version:
        return env_version
    try:
        with open(VERSION_PATH, encoding="utf-8") as f:
            return f.read().strip() or "dev"
    except OSError:
        return "dev"


APP_VERSION = load_app_version()

app = Flask(__name__)


@app.context_processor
def inject_app_version():
    return {"app_version": APP_VERSION}


def save_cover_upload(file_storage):
    """Guarda la foto de portada subida y devuelve su URL, o None si no hay archivo."""
    if not file_storage or not file_storage.filename:
        return None
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in ALLOWED_COVER_EXT:
        return None
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(UPLOAD_DIR, filename))
    return url_for("uploaded_cover", filename=filename)


@app.route("/uploads/<path:filename>")
def uploaded_cover(filename):
    return send_from_directory(UPLOAD_DIR, filename)


def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()


def make_book_info(
    title,
    author="",
    publisher="",
    published_year="",
    cover_url="",
    description="",
    source="",
):
    if not title:
        return None
    return {
        "title": title,
        "author": author,
        "publisher": publisher,
        "published_year": str(published_year or "")[:4],
        "cover_url": cover_url,
        "description": description,
        "source": source,
    }


def lookup_google_books(isbn):
    params = {"q": f"isbn:{isbn}"}
    api_key = os.environ.get("GOOGLE_BOOKS_API_KEY")
    if api_key:
        params["key"] = api_key

    try:
        r = requests.get(
            "https://www.googleapis.com/books/v1/volumes",
            params=params,
            timeout=5,
        )
        r.raise_for_status()
        items = r.json().get("items") or []
        if items:
            info = items[0].get("volumeInfo", {})
            return make_book_info(
                title=info.get("title", ""),
                author=", ".join(info.get("authors", [])),
                publisher=info.get("publisher", ""),
                published_year=info.get("publishedDate", ""),
                cover_url=info.get("imageLinks", {}).get("thumbnail", ""),
                description=info.get("description", ""),
                source="Google Books",
            )
    except requests.RequestException:
        pass
    return None


def lookup_open_library_books(isbn):
    try:
        r = requests.get(
            "https://openlibrary.org/api/books",
            params={"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
            timeout=5,
        )
        r.raise_for_status()
        data = r.json().get(f"ISBN:{isbn}")
        if data:
            return make_book_info(
                title=data.get("title", ""),
                author=", ".join(a["name"] for a in data.get("authors", [])),
                publisher=", ".join(p["name"] for p in data.get("publishers", [])),
                published_year=(data.get("publish_date") or "")[-4:],
                cover_url=data.get("cover", {}).get("medium", ""),
                source="Open Library Books",
            )
    except requests.RequestException:
        pass
    return None


def lookup_open_library_search(isbn):
    try:
        r = requests.get(
            "https://openlibrary.org/search.json",
            params={"isbn": isbn, "limit": 1},
            timeout=5,
        )
        r.raise_for_status()
        docs = r.json().get("docs") or []
        if docs:
            doc = docs[0]
            cover_id = doc.get("cover_i")
            cover_url = (
                f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg"
                if cover_id
                else ""
            )
            return make_book_info(
                title=doc.get("title", ""),
                author=", ".join(doc.get("author_name", [])),
                publisher=", ".join((doc.get("publisher") or [])[:2]),
                published_year=doc.get("first_publish_year", ""),
                cover_url=cover_url,
                source="Open Library Search",
            )
    except requests.RequestException:
        pass
    return None


def make_isbn_search_links(isbn):
    query = quote_plus(f"ISBN {isbn}")
    return [
        {"label": "Google", "url": f"https://www.google.com/search?q={query}"},
        {"label": "Buscalibre", "url": f"https://www.buscalibre.com/libros/search?q={isbn}"},
        {"label": "IberLibro", "url": f"https://www.iberlibro.com/servlet/SearchResults?isbn={isbn}"},
        {"label": "WorldCat", "url": f"https://search.worldcat.org/isbn/{isbn}"},
        {"label": "Open Library", "url": f"https://openlibrary.org/search?isbn={isbn}"},
    ]


def lookup_isbn(isbn):
    for provider in (
        lookup_google_books,
        lookup_open_library_books,
        lookup_open_library_search,
    ):
        info = provider(isbn)
        if info:
            return info
    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    field = request.args.get("field", "todo")
    db = get_db()
    select_books = """
        SELECT b.*, l.borrower_name AS current_borrower, l.loaned_at AS current_loaned_at
        FROM books b
        LEFT JOIN loans l ON l.book_id = b.id AND l.returned_at IS NULL
    """

    if not q:
        rows = db.execute(f"{select_books} ORDER BY b.added_at DESC").fetchall()
    else:
        like = f"%{q}%"
        if field == "titulo":
            rows = db.execute(
                f"{select_books} WHERE b.title LIKE ? ORDER BY b.title",
                (like,),
            ).fetchall()
        elif field == "autor":
            rows = db.execute(
                f"{select_books} WHERE b.author LIKE ? ORDER BY b.author",
                (like,),
            ).fetchall()
        elif field == "isbn":
            rows = db.execute(
                f"{select_books} WHERE b.isbn LIKE ? ORDER BY b.title",
                (like,),
            ).fetchall()
        elif field == "ubicacion":
            rows = db.execute(
                f"{select_books} WHERE b.location LIKE ? ORDER BY b.title",
                (like,),
            ).fetchall()
        elif field == "prestado":
            rows = db.execute(
                f"{select_books} WHERE l.borrower_name LIKE ? ORDER BY l.loaned_at DESC",
                (like,),
            ).fetchall()
        else:
            rows = db.execute(
                f"""{select_books}
                   WHERE b.title LIKE ? OR b.author LIKE ? OR b.isbn LIKE ?
                      OR b.location LIKE ? OR l.borrower_name LIKE ?
                   ORDER BY b.added_at DESC""",
                (like, like, like, like, like),
            ).fetchall()

    return render_template("partials/_book_list.html", books=rows)


@app.route("/scan")
def scan():
    return render_template("scan.html")


def lookup_local_book(isbn):
    db = get_db()
    row = db.execute("SELECT * FROM books WHERE isbn = ?", (isbn,)).fetchone()
    if not row:
        return None
    return make_book_info(
        title=row["title"],
        author=row["author"] or "",
        publisher=row["publisher"] or "",
        published_year=row["published_year"] or "",
        cover_url=row["cover_url"] or "",
        description=row["description"] or "",
        source="Biblioteca",
    )


@app.route("/api/lookup/<isbn>")
def api_lookup(isbn):
    isbn = "".join(c for c in isbn if c.isalnum())
    search_links = make_isbn_search_links(isbn)
    info = lookup_local_book(isbn) or lookup_isbn(isbn)
    if not info:
        return jsonify({"found": False, "isbn": isbn, "search_links": search_links})
    info["found"] = True
    info["isbn"] = isbn
    info["search_links"] = search_links
    return jsonify(info)


@app.route("/books", methods=["POST"])
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
           ON CONFLICT(isbn) DO UPDATE SET location = excluded.location
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
    return redirect(url_for("book_detail", book_id=book_id))


def render_loans_partial(book_id):
    db = get_db()
    book = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    current_loan = db.execute(
        "SELECT * FROM loans "
        "WHERE book_id = ? AND returned_at IS NULL "
        "ORDER BY loaned_at DESC LIMIT 1",
        (book_id,),
    ).fetchone()
    loans = db.execute(
        "SELECT * FROM loans WHERE book_id = ? ORDER BY loaned_at DESC, id DESC",
        (book_id,),
    ).fetchall()
    return render_template(
        "partials/_loans.html",
        book=book,
        current_loan=current_loan,
        loans=loans,
    )


@app.route("/books/<int:book_id>")
def book_detail(book_id):
    db = get_db()
    book = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        return "Libro no encontrado", 404
    current_loan = db.execute(
        "SELECT * FROM loans "
        "WHERE book_id = ? AND returned_at IS NULL "
        "ORDER BY loaned_at DESC LIMIT 1",
        (book_id,),
    ).fetchone()
    loans = db.execute(
        "SELECT * FROM loans WHERE book_id = ? ORDER BY loaned_at DESC, id DESC",
        (book_id,),
    ).fetchall()
    return render_template(
        "book_detail.html",
        book=book,
        current_loan=current_loan,
        loans=loans,
    )


@app.route("/books/<int:book_id>", methods=["POST"])
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

    db.execute(
        """UPDATE books SET
           title=?, author=?, publisher=?, published_year=?, location=?, cover_url=?
           WHERE id=?""",
        (
            request.form.get("title", "").strip(),
            request.form.get("author", "").strip(),
            request.form.get("publisher", "").strip(),
            request.form.get("published_year", "").strip(),
            request.form.get("location", "").strip(),
            cover_url,
            book_id,
        ),
    )
    db.commit()
    return redirect(url_for("book_detail", book_id=book_id))


@app.route("/books/<int:book_id>/delete", methods=["POST"])
def delete_book(book_id):
    db = get_db()
    db.execute("DELETE FROM books WHERE id = ?", (book_id,))
    db.commit()
    return redirect(url_for("index"))


@app.route("/books/<int:book_id>/loans", methods=["POST"])
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
    return render_loans_partial(book_id)


@app.route("/books/<int:book_id>/loans/<int:loan_id>/return", methods=["POST"])
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
        "UPDATE loans SET returned_at = ?, return_notes = ? "
        "WHERE id = ? AND book_id = ?",
        (returned_at, return_notes, loan_id, book_id),
    )
    db.commit()
    return render_loans_partial(book_id)


with app.app_context():
    init_db()

if __name__ == "__main__":
    ssl_context = "adhoc" if os.environ.get("HTTPS_ADHOC") else None
    app.run(host="0.0.0.0", port=5000, debug=True, ssl_context=ssl_context)
