import json
import os
import re
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import requests
from flask import url_for
from .db import get_db, DB_PATH


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


def clean_marc_year(raw):
    if not raw:
        return raw
    match = re.search(r"\d{4}", raw)
    if match:
        return match.group(0)
    return raw.strip(" [].,")


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
                f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg" if cover_id else ""
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
            published_year=clean_marc_year(year),
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
        from urllib.parse import unquote as _unquote
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
            headers={"X-XSRF-TOKEN": _unquote(token), "Accept": "application/json"},
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


# Subidas de portada
UPLOAD_DIR = os.path.join(os.path.dirname(DB_PATH), "uploads")
ALLOWED_COVER_EXT = {"jpg", "jpeg", "png", "webp", "gif"}


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
    return url_for("uploads.uploaded_cover", filename=filename)


# Búsqueda de libros
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


def guess_title_from_ocr_text(text):
    """La línea alfabética más larga suele ser el título en la portada"""
    candidates = [
        line.strip()
        for line in text.splitlines()
        if len(line.strip()) >= 3 and any(c.isalpha() for c in line)
    ]
    if not candidates:
        return None
    return max(candidates, key=len)


def get_dashboard_stats(db):
    total = db.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    loaned = db.execute(
        "SELECT COUNT(DISTINCT book_id) FROM loans WHERE returned_at IS NULL"
    ).fetchone()[0]
    reading = db.execute(
        "SELECT COUNT(DISTINCT book_id) FROM reading_entries WHERE finished_at IS NULL"
    ).fetchone()[0]
    finished = db.execute(
        "SELECT COUNT(DISTINCT book_id) FROM reading_entries WHERE finished_at IS NOT NULL"
    ).fetchone()[0]
    pending = db.execute(
        """
        SELECT COUNT(*) FROM books b
        WHERE NOT EXISTS (SELECT 1 FROM reading_entries r WHERE r.book_id = b.id)
        """
    ).fetchone()[0]
    return {
        "total": total,
        "available": max(0, total - loaned),
        "loaned": loaned,
        "reading": reading,
        "finished": finished,
        "pending": pending,
    }


def get_active_loans(db):
    return db.execute(
        """
        SELECT l.*, b.title, b.cover_url, b.id AS book_id
        FROM loans l
        JOIN books b ON b.id = l.book_id
        WHERE l.returned_at IS NULL
        ORDER BY l.loaned_at DESC
        """
    ).fetchall()


def get_loan_history(db):
    return db.execute(
        """
        SELECT l.*, b.title, b.cover_url, b.id AS book_id
        FROM loans l
        JOIN books b ON b.id = l.book_id
        WHERE l.returned_at IS NOT NULL
        ORDER BY l.returned_at DESC
        """
    ).fetchall()


def get_active_readings(db):
    return db.execute(
        """
        SELECT r.*, b.title, b.cover_url, b.id AS book_id
        FROM reading_entries r
        JOIN books b ON b.id = r.book_id
        WHERE r.finished_at IS NULL
        ORDER BY r.started_at DESC
        """
    ).fetchall()


def get_finished_readings(db):
    return db.execute(
        """
        SELECT r.*, b.title, b.cover_url, b.id AS book_id
        FROM reading_entries r
        JOIN books b ON b.id = r.book_id
        WHERE r.finished_at IS NOT NULL
        ORDER BY r.finished_at DESC
        """
    ).fetchall()


def get_top_locations(db, limit=8):
    return db.execute(
        """
        SELECT TRIM(location) AS loc, COUNT(*) AS n
        FROM books
        WHERE TRIM(COALESCE(location, '')) != ''
        GROUP BY loc
        ORDER BY n DESC, loc COLLATE NOCASE
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
