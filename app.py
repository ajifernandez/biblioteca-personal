import json
import os
import re
import sqlite3
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import quote_plus, unquote

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


def migrate_drop_isbn_unique(conn):
    """Libros con el mismo ISBN pueden representar varios ejemplares físicos,
    así que la restricción UNIQUE original sobre isbn ya no aplica."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='books'"
    ).fetchone()
    if not row or "UNIQUE" not in row[0]:
        return
    conn.executescript("""
        CREATE TABLE books_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            isbn TEXT,
            title TEXT NOT NULL,
            author TEXT,
            publisher TEXT,
            published_year TEXT,
            cover_url TEXT,
            description TEXT,
            location TEXT,
            added_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO books_new SELECT * FROM books;
        DROP TABLE books;
        ALTER TABLE books_new RENAME TO books;
    """)
    conn.commit()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    migrate_drop_isbn_unique(conn)
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
    if cover_url.startswith("http://"):
        cover_url = "https://" + cover_url[len("http://"):]
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


MARC_NS = {
    "srw": "http://www.loc.gov/zing/srw/",
    "marc": "http://www.loc.gov/MARC21/slim",
}


def marc_values(record, tag, code):
    values = []
    for datafield in record.findall(f'marc:datafield[@tag="{tag}"]', MARC_NS):
        for subfield in datafield.findall(f'marc:subfield[@code="{code}"]', MARC_NS):
            if subfield.text and subfield.text.strip():
                values.append(subfield.text.strip())
    return values


def lookup_bne(isbn):
    try:
        r = requests.get(
            "https://catalogo.bne.es/view/sru/34BNE_INST",
            params={
                "operation": "searchRetrieve",
                "version": "1.2",
                "query": f'alma.isbn="{isbn}"',
                "recordSchema": "marcxml",
                "maximumRecords": 1,
            },
            timeout=8,
        )
        r.raise_for_status()
        record = ET.fromstring(r.content).find(".//srw:recordData/marc:record", MARC_NS)
        if record is None:
            return None
        title = " ".join(marc_values(record, "245", "a") + marc_values(record, "245", "b")).rstrip(" :")
        publisher = ", ".join(marc_values(record, "264", "b") or marc_values(record, "260", "b"))
        year = " ".join(marc_values(record, "264", "c") or marc_values(record, "260", "c"))
        return make_book_info(
            title=title,
            author=", ".join(marc_values(record, "100", "a")),
            publisher=publisher,
            published_year=year,
            source="Biblioteca Nacional de España",
        )
    except (requests.RequestException, ET.ParseError):
        pass
    return None


def lookup_buscalibre(isbn):
    try:
        r = requests.get(
            "https://www.buscalibre.es/libros/search",
            params={"q": isbn},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        r.raise_for_status()
        if "/p/" not in r.url:
            return None
        for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S):
            try:
                data = json.loads(block)
            except ValueError:
                continue
            if data.get("@type") != "Product":
                continue
            author = data.get("author") or {}
            publisher = data.get("publisher") or {}
            return make_book_info(
                title=data.get("name", ""),
                author=author.get("name", "") if isinstance(author, dict) else "",
                publisher=publisher.get("name", "") if isinstance(publisher, dict) else "",
                cover_url=data.get("image", ""),
                description=data.get("description", ""),
                source="Buscalibre",
            )
    except requests.RequestException:
        pass
    return None


def lookup_pluton(isbn):
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        session.get("https://pedidos.plutonediciones.com/es", timeout=8)
        token = session.cookies.get("XSRF-TOKEN")
        if not token:
            return None
        r = session.post(
            "https://pedidos.plutonediciones.com/es/search/get-for-typeahead",
            json={
                "search": isbn,
                "selectFields": ["idarticulo", "descripcion", "autor", "ean"],
                "resultsPerPage": 5,
            },
            headers={"X-XSRF-TOKEN": unquote(token), "Accept": "application/json"},
            timeout=8,
        )
        r.raise_for_status()
        for item in r.json():
            if isinstance(item, dict) and item.get("ean") == isbn:
                return make_book_info(
                    title=item.get("descripcion", ""),
                    author=item.get("autor", ""),
                    publisher="Plutón Ediciones",
                    source="Plutón Ediciones",
                )
    except (requests.RequestException, ValueError):
        pass
    return None


LOOKUP_PROVIDERS = [
    ("local", "tu biblioteca", None),
    ("buscalibre", "Buscalibre", lookup_buscalibre),
    ("google", "Google Books", lookup_google_books),
    ("openlibrary_books", "Open Library", lookup_open_library_books),
    ("openlibrary_search", "Open Library (búsqueda)", lookup_open_library_search),
    ("bne", "Biblioteca Nacional de España", lookup_bne),
    ("pluton", "Plutón Ediciones", lookup_pluton),
]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/ubicaciones")
def locations():
    db = get_db()
    rows = db.execute(
        """SELECT
               CASE WHEN TRIM(COALESCE(location, '')) = '' THEN NULL ELSE TRIM(location) END AS loc,
               COUNT(*) AS n
           FROM books
           GROUP BY loc
           ORDER BY loc IS NULL, loc COLLATE NOCASE"""
    ).fetchall()
    return render_template("locations.html", locations=rows)


@app.route("/api/locations")
def api_locations():
    db = get_db()
    rows = db.execute(
        """SELECT DISTINCT TRIM(location) AS loc FROM books
           WHERE TRIM(COALESCE(location, '')) != ''
           ORDER BY loc COLLATE NOCASE"""
    ).fetchall()
    return jsonify([row["loc"] for row in rows])


SELECT_BOOKS_SQL = """
    SELECT b.*, l.borrower_name AS current_borrower, l.loaned_at AS current_loaned_at,
           r.started_at AS reading_started_at
    FROM books b
    LEFT JOIN loans l ON l.book_id = b.id AND l.returned_at IS NULL
    LEFT JOIN reading_entries r ON r.book_id = b.id AND r.finished_at IS NULL
"""


def find_books(q, field):
    db = get_db()
    if field == "sin_ubicacion":
        return db.execute(
            f"{SELECT_BOOKS_SQL} WHERE b.location IS NULL OR TRIM(b.location) = '' ORDER BY b.title"
        ).fetchall()
    if not q:
        return db.execute(f"{SELECT_BOOKS_SQL} ORDER BY b.added_at DESC").fetchall()

    like = f"%{q}%"
    if field == "titulo":
        return db.execute(
            f"{SELECT_BOOKS_SQL} WHERE b.title LIKE ? ORDER BY b.title",
            (like,),
        ).fetchall()
    if field == "autor":
        return db.execute(
            f"{SELECT_BOOKS_SQL} WHERE b.author LIKE ? ORDER BY b.author",
            (like,),
        ).fetchall()
    if field == "isbn":
        return db.execute(
            f"{SELECT_BOOKS_SQL} WHERE b.isbn LIKE ? ORDER BY b.title",
            (like,),
        ).fetchall()
    if field == "ubicacion":
        return db.execute(
            f"{SELECT_BOOKS_SQL} WHERE b.location LIKE ? ORDER BY b.title",
            (like,),
        ).fetchall()
    if field == "prestado":
        return db.execute(
            f"{SELECT_BOOKS_SQL} WHERE l.borrower_name LIKE ? ORDER BY l.loaned_at DESC",
            (like,),
        ).fetchall()
    return db.execute(
        f"""{SELECT_BOOKS_SQL}
           WHERE b.title LIKE ? OR b.author LIKE ? OR b.isbn LIKE ?
              OR b.location LIKE ? OR l.borrower_name LIKE ?
           ORDER BY b.added_at DESC""",
        (like, like, like, like, like),
    ).fetchall()


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    field = request.args.get("field", "todo")
    view = request.args.get("view", "grid")
    return render_template("partials/_book_list.html", books=find_books(q, field), view=view)


@app.route("/books/bulk-location", methods=["POST"])
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


@app.route("/books/bulk-loan", methods=["POST"])
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


def lookup_isbn_external(isbn):
    info = None
    for _, _, provider in LOOKUP_PROVIDERS:
        if provider is None:
            continue
        result = provider(isbn)
        if not result:
            continue
        if info is None:
            info = result
        elif not info["cover_url"] and result["cover_url"]:
            info["cover_url"] = result["cover_url"]
        if info["cover_url"]:
            break
    return info


@app.route("/books/<int:book_id>/refill", methods=["POST"])
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


@app.route("/scan")
def scan():
    return render_template("scan.html")


def lookup_local_book(isbn, exclude_book_id=None):
    db = get_db()
    if exclude_book_id is None:
        row = db.execute("SELECT * FROM books WHERE isbn = ?", (isbn,)).fetchone()
    else:
        row = db.execute(
            "SELECT * FROM books WHERE isbn = ? AND id != ?", (isbn, exclude_book_id)
        ).fetchone()
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


@app.route("/api/lookup-providers")
def api_lookup_providers():
    return jsonify([{"id": pid, "label": label} for pid, label, _ in LOOKUP_PROVIDERS])


@app.route("/api/lookup/<provider_id>/<isbn>")
def api_lookup_provider(provider_id, isbn):
    isbn = "".join(c for c in isbn if c.isalnum())
    match = next((p for p in LOOKUP_PROVIDERS if p[0] == provider_id), None)
    if not match:
        return jsonify({"found": False, "isbn": isbn}), 404

    if provider_id == "local":
        exclude_book_id = request.args.get("exclude_book_id", type=int)
        info = lookup_local_book(isbn, exclude_book_id)
    else:
        info = match[2](isbn)

    if not info:
        return jsonify({"found": False, "isbn": isbn})
    info["found"] = True
    info["isbn"] = isbn
    return jsonify(info)


@app.route("/api/isbn-search-links/<isbn>")
def api_isbn_search_links(isbn):
    isbn = "".join(c for c in isbn if c.isalnum())
    return jsonify(make_isbn_search_links(isbn))


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


def render_reading_partial(book_id):
    db = get_db()
    book = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    current_reading = db.execute(
        "SELECT * FROM reading_entries "
        "WHERE book_id = ? AND finished_at IS NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (book_id,),
    ).fetchone()
    reading_entries = db.execute(
        "SELECT * FROM reading_entries WHERE book_id = ? ORDER BY started_at DESC, id DESC",
        (book_id,),
    ).fetchall()
    return render_template(
        "partials/_reading.html",
        book=book,
        current_reading=current_reading,
        reading_entries=reading_entries,
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
    current_reading = db.execute(
        "SELECT * FROM reading_entries "
        "WHERE book_id = ? AND finished_at IS NULL "
        "ORDER BY started_at DESC LIMIT 1",
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


@app.route("/books/<int:book_id>/reading", methods=["POST"])
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
    return render_reading_partial(book_id)


@app.route("/books/<int:book_id>/reading/<int:entry_id>/finish", methods=["POST"])
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
        "UPDATE reading_entries SET finished_at = ?, rating = ?, notes = ? "
        "WHERE id = ? AND book_id = ?",
        (finished_at, rating, notes, entry_id, book_id),
    )
    db.commit()
    return render_reading_partial(book_id)


with app.app_context():
    init_db()

if __name__ == "__main__":
    ssl_context = "adhoc" if os.environ.get("HTTPS_ADHOC") else None
    app.run(host="0.0.0.0", port=5000, debug=True, ssl_context=ssl_context)
