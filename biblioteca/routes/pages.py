import sqlite3
from flask import Blueprint, redirect, render_template, request, url_for

from ..db import get_db
from ..services import (
    find_books,
    get_active_loans,
    get_active_readings,
    get_books_by_year,
    get_dashboard_stats,
    get_finished_readings,
    get_loan_history,
    get_top_authors,
    get_top_borrowers,
    get_top_locations,
)

pages = Blueprint("pages", __name__)


@pages.route("/", endpoint="index")
def index():
    db = get_db()
    return render_template("index.html", stats=get_dashboard_stats(db))


@pages.route("/search", endpoint="search")
def search():
    q = request.args.get("q", "").strip()
    field = request.args.get("field", "todo")
    view = request.args.get("view", "grid")
    status = request.args.get("status", "todos")
    location = request.args.get("location", "").strip()
    author = request.args.get("author", "").strip()
    year_from = request.args.get("year_from", "").strip()
    year_to = request.args.get("year_to", "").strip()
    sort = request.args.get("sort", "added_at")
    books = find_books(
        q=q,
        field=field,
        status=status,
        location=location,
        author=author,
        year_from=year_from,
        year_to=year_to,
        sort=sort,
    )
    return render_template("partials/_book_list.html", books=books, view=view)


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
    db = get_db()
    rows = db.execute(
        """
        SELECT l.id, l.name,
               COUNT(b.id) AS n
        FROM locations l
        LEFT JOIN books b ON TRIM(COALESCE(b.location, '')) = l.name
        GROUP BY l.id, l.name
        ORDER BY l.name COLLATE NOCASE
        """
    ).fetchall()
    return render_template("locations.html", locations=rows)


@pages.route("/ubicaciones", endpoint="create_location", methods=["POST"])
def create_location():
    db = get_db()
    name = request.form.get("name", "").strip()
    if not name:
        return render_template("locations.html", locations=db.execute(
            """
            SELECT l.id, l.name, COUNT(b.id) AS n
            FROM locations l
            LEFT JOIN books b ON TRIM(COALESCE(b.location, '')) = l.name
            GROUP BY l.id, l.name
            ORDER BY l.name COLLATE NOCASE
            """
        ).fetchall(), error="El nombre es obligatorio"), 400
    try:
        db.execute("INSERT INTO locations (name) VALUES (?)", (name,))
        db.commit()
    except sqlite3.IntegrityError:
        return render_template("locations.html", locations=db.execute(
            """
            SELECT l.id, l.name, COUNT(b.id) AS n
            FROM locations l
            LEFT JOIN books b ON TRIM(COALESCE(b.location, '')) = l.name
            GROUP BY l.id, l.name
            ORDER BY l.name COLLATE NOCASE
            """
        ).fetchall(), error="Ya existe esa ubicación"), 400
    return redirect(url_for("pages.locations"))


@pages.route("/ubicaciones/<int:location_id>/editar", endpoint="edit_location", methods=["POST"])
def edit_location(location_id):
    db = get_db()
    location = db.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()
    if not location:
        return "Ubicación no encontrada", 404
    old_name = location["name"]
    new_name = request.form.get("name", "").strip()
    if not new_name:
        return redirect(url_for("pages.locations"))
    try:
        db.execute("UPDATE locations SET name = ? WHERE id = ?", (new_name, location_id))
        db.execute(
            "UPDATE books SET location = ? WHERE TRIM(COALESCE(location, '')) = ?",
            (new_name, old_name),
        )
        db.commit()
    except sqlite3.IntegrityError:
        pass
    return redirect(url_for("pages.locations"))


@pages.route("/ubicaciones/<int:location_id>/eliminar", endpoint="delete_location", methods=["POST"])
def delete_location(location_id):
    db = get_db()
    location = db.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()
    if not location:
        return "Ubicación no encontrada", 404
    count = db.execute(
        "SELECT COUNT(*) FROM books WHERE TRIM(COALESCE(location, '')) = ?",
        (location["name"],),
    ).fetchone()[0]
    if count > 0:
        return redirect(url_for("pages.locations"))
    db.execute("DELETE FROM locations WHERE id = ?", (location_id,))
    db.commit()
    return redirect(url_for("pages.locations"))


@pages.route("/scan", endpoint="scan")
def scan():
    return render_template("scan.html")


@pages.route("/lecturas", endpoint="readings")
def readings():
    db = get_db()
    return render_template(
        "readings.html",
        current=get_active_readings(db),
        finished=get_finished_readings(db),
    )


@pages.route("/prestamos", endpoint="loans")
def loans():
    db = get_db()
    return render_template(
        "loans.html",
        active=get_active_loans(db),
        history=get_loan_history(db),
    )


@pages.route("/estadisticas", endpoint="stats")
def stats():
    db = get_db()
    return render_template(
        "stats.html",
        stats=get_dashboard_stats(db),
        top_locations=get_top_locations(db),
        top_authors=get_top_authors(db),
        top_borrowers=get_top_borrowers(db),
        years=get_books_by_year(db),
    )
