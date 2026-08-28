from flask import Blueprint, jsonify, request
from PIL import Image
import pytesseract

from ..db import get_db
from ..services import (
    LOOKUP_PROVIDERS,
    lookup_local_book,
    make_isbn_search_links,
    guess_title_from_ocr_text,
)

api = Blueprint("api", __name__)


@api.route("/locations")
def api_locations():
    db = get_db()
    rows = db.execute(
        """SELECT DISTINCT TRIM(location) AS loc FROM books
           WHERE TRIM(COALESCE(location, '')) != ''
           ORDER BY loc COLLATE NOCASE"""
    ).fetchall()
    return jsonify([row["loc"] for row in rows])


@api.route("/lookup-providers")
def api_lookup_providers():
    return jsonify([{"id": pid, "label": label} for pid, label, _ in LOOKUP_PROVIDERS])


@api.route("/lookup/<provider_id>/<isbn>")
def api_lookup_provider(provider_id, isbn):
    isbn = "".join(c for c in isbn if c.isalnum())
    match = next((p for p in LOOKUP_PROVIDERS if p[0] == provider_id), None)
    if not match:
        return jsonify({"found": False, "isbn": isbn}), 404

    if provider_id == "local":
        exclude_book_id = request.args.get("exclude_book_id", type=int)
        info = lookup_local_book(isbn, exclude_book_id)
    else:
        provider = match[2]
        info = provider(isbn) if provider else None

    if not info:
        return jsonify({"found": False, "isbn": isbn})
    info["found"] = True
    info["isbn"] = isbn
    return jsonify(info)


@api.route("/isbn-search-links/<isbn>")
def api_isbn_search_links(isbn):
    isbn = "".join(c for c in isbn if c.isalnum())
    return jsonify(make_isbn_search_links(isbn))


@api.route("/ocr-cover", methods=["POST"])
def ocr_cover():
    photo = request.files.get("photo")
    if not photo or not photo.filename:
        return jsonify({"error": "no_photo"}), 400

    try:
        image = Image.open(photo.stream).convert("L")
        text = pytesseract.image_to_string(image, lang="spa+eng")
    except pytesseract.TesseractNotFoundError:
        return jsonify({"error": "ocr_unavailable"}), 503
    except Exception:
        return jsonify({"error": "ocr_failed"}), 422

    text = text.strip()
    return jsonify({"text": text, "title_guess": guess_title_from_ocr_text(text)})
