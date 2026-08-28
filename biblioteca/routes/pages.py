from flask import Blueprint, render_template, request

from ..services import find_books

pages = Blueprint("pages", __name__)


@pages.route("/", endpoint="index")
def index():
    return render_template("index.html")


@pages.route("/search", endpoint="search")
def search():
    q = request.args.get("q", "").strip()
    field = request.args.get("field", "todo")
    view = request.args.get("view", "grid")
    return render_template("partials/_book_list.html", books=find_books(q, field), view=view)


@pages.route("/csv", endpoint="csv_page")
def csv_page():
    return render_template(
        "csv.html",
        imported=request.args.get("imported"),
        inserted=request.args.get("inserted"),
        updated=request.args.get("updated"),
        skipped=request.args.get("skipped"),
        error=request.args.get("error"),
    )


@pages.route("/ubicaciones", endpoint="locations")
def locations():
    from ..db import get_db

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


@pages.route("/scan", endpoint="scan")
def scan():
    return render_template("scan.html")
