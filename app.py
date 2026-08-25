import os
import sqlite3
import uuid
from datetime import datetime

import requests
from flask import Flask, g, jsonify, redirect, render_template, request, send_from_directory, url_for

DB_PATH = os.environ.get("DB_PATH", "/data/biblioteca.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")
UPLOAD_DIR = os.path.join(os.path.dirname(DB_PATH), "uploads")
ALLOWED_COVER_EXT = {"jpg", "jpeg", "png", "webp", "gif"}

app = Flask(__name__)


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


def lookup_isbn(isbn):
    """Google Books first, Open Library fallback. Returns dict or None."""
    try:
        r = requests.get(
            "https://www.googleapis.com/books/v1/volumes",
            params={"q": f"isbn:{isbn}"},
            timeout=5,
        )
        r.raise_for_status()
        items = r.json().get("items")
        if items:
            info = items[0]["volumeInfo"]
            return {
                "title": info.get("title", ""),
                "author": ", ".join(info.get("authors", [])),
                "publisher": info.get("publisher", ""),
                "published_year": (info.get("publishedDate") or "")[:4],
                "cover_url": info.get("imageLinks", {}).get("thumbnail", ""),
                "description": info.get("description", ""),
            }
    except requests.RequestException:
        pass

    try:
        r = requests.get(
            "https://openlibrary.org/api/books",
            params={"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
            timeout=5,
        )
        r.raise_for_status()
        data = r.json().get(f"ISBN:{isbn}")
        if data:
            return {
                "title": data.get("title", ""),
                "author": ", ".join(a["name"] for a in data.get("authors", [])),
                "publisher": ", ".join(p["name"] for p in data.get("publishers", [])),
                "published_year": (data.get("publish_date") or "")[-4:],
                "cover_url": data.get("cover", {}).get("medium", ""),
                "description": "",
            }
    except requests.RequestException:
        pass

    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    field = request.args.get("field", "todo")
    db = get_db()

    if not q:
        rows = db.execute("SELECT * FROM books ORDER BY added_at DESC").fetchall()
    else:
        like = f"%{q}%"
        if field == "titulo":
            rows = db.execute(
                "SELECT * FROM books WHERE title LIKE ? ORDER BY title", (like,)
            ).fetchall()
        elif field == "autor":
            rows = db.execute(
                "SELECT * FROM books WHERE author LIKE ? ORDER BY author", (like,)
            ).fetchall()
        elif field == "isbn":
            rows = db.execute(
                "SELECT * FROM books WHERE isbn LIKE ?", (like,)
            ).fetchall()
        elif field == "ubicacion":
            rows = db.execute(
                "SELECT * FROM books WHERE location LIKE ?", (like,)
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT * FROM books
                   WHERE title LIKE ? OR author LIKE ? OR isbn LIKE ? OR location LIKE ?
                   ORDER BY added_at DESC""",
                (like, like, like, like),
            ).fetchall()

    return render_template("partials/_book_list.html", books=rows)


@app.route("/scan")
def scan():
    return render_template("scan.html")


@app.route("/api/lookup/<isbn>")
def api_lookup(isbn):
    isbn = "".join(c for c in isbn if c.isalnum())
    info = lookup_isbn(isbn)
    if not info:
        return jsonify({"found": False, "isbn": isbn})
    info["found"] = True
    info["isbn"] = isbn
    return jsonify(info)


@app.route("/books", methods=["POST"])
def create_book():
    db = get_db()
    isbn = request.form.get("isbn", "").strip() or None
    title = request.form.get("title", "").strip()
    if not title:
        return "El título es obligatorio", 400

    cover_url = save_cover_upload(request.files.get("cover_file")) or request.form.get("cover_url", "").strip()

    cur = db.execute(
        """INSERT INTO books (isbn, title, author, publisher, published_year, cover_url, description, location)
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


@app.route("/books/<int:book_id>")
def book_detail(book_id):
    db = get_db()
    book = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        return "Libro no encontrado", 404
    history = db.execute(
        "SELECT * FROM reading_log WHERE book_id = ? ORDER BY started_at DESC",
        (book_id,),
    ).fetchall()
    return render_template("book_detail.html", book=book, history=history)


@app.route("/books/<int:book_id>", methods=["POST"])
def update_book(book_id):
    db = get_db()
    existing = db.execute("SELECT cover_url FROM books WHERE id = ?", (book_id,)).fetchone()
    if not existing:
        return "Libro no encontrado", 404

    cover_url = (
        save_cover_upload(request.files.get("cover_file"))
        or request.form.get("cover_url", "").strip()
        or existing["cover_url"]
    )

    db.execute(
        """UPDATE books SET title=?, author=?, publisher=?, published_year=?, location=?, cover_url=?
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


@app.route("/books/<int:book_id>/history", methods=["POST"])
def add_history(book_id):
    db = get_db()
    status = request.form.get("status")
    today = datetime.now().strftime("%Y-%m-%d")

    if status == "leyendo":
        db.execute(
            "INSERT INTO reading_log (book_id, status, started_at) VALUES (?, 'leyendo', ?)",
            (book_id, today),
        )
    elif status == "leido":
        open_entry = db.execute(
            "SELECT id FROM reading_log WHERE book_id = ? AND status = 'leyendo' AND finished_at IS NULL ORDER BY started_at DESC LIMIT 1",
            (book_id,),
        ).fetchone()
        rating = request.form.get("rating") or None
        notes = request.form.get("notes", "").strip()
        if open_entry:
            db.execute(
                "UPDATE reading_log SET status='leido', finished_at=?, rating=?, notes=? WHERE id=?",
                (today, rating, notes, open_entry["id"]),
            )
        else:
            db.execute(
                "INSERT INTO reading_log (book_id, status, started_at, finished_at, rating, notes) VALUES (?, 'leido', ?, ?, ?, ?)",
                (book_id, today, today, rating, notes),
            )
    db.commit()

    book = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    history = db.execute(
        "SELECT * FROM reading_log WHERE book_id = ? ORDER BY started_at DESC",
        (book_id,),
    ).fetchall()
    return render_template("partials/_history.html", book=book, history=history)


with app.app_context():
    init_db()

if __name__ == "__main__":
    ssl_context = "adhoc" if os.environ.get("HTTPS_ADHOC") else None
    app.run(host="0.0.0.0", port=5000, debug=True, ssl_context=ssl_context)
