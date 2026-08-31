import os
import sqlite3
from flask import g

DB_PATH = os.environ.get("DB_PATH", "/data/biblioteca.db")
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "schema.sql")


def _table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _column_exists(conn, table, column):
    for r in conn.execute(f"PRAGMA table_info({table})").fetchall():
        if r[1] == column:
            return True
    return False


def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            # Mejoras de concurrencia; no falla si no están soportadas
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=3000")
        except sqlite3.Error:
            pass
        g.db = conn
    return g.db


def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# Migraciones simples y idempotentes

def migrate_drop_isbn_unique(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='books'"
    ).fetchone()
    if not row:
        return
    sql = row[0] or ""
    if "UNIQUE" not in sql:
        return
    conn.executescript(
        """
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
        """
    )
    conn.commit()


def migrate_locations_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            color TEXT,
            icon TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


def migrate_reading_status(conn):
    if not _table_exists(conn, "reading_entries"):
        return
    if not _column_exists(conn, "reading_entries", "status"):
        conn.execute(
            "ALTER TABLE reading_entries ADD COLUMN status TEXT NOT NULL DEFAULT 'reading'"
        )
        conn.execute(
            """
            UPDATE reading_entries
            SET status = CASE
                WHEN finished_at IS NOT NULL THEN 'finished'
                ELSE 'reading'
            END
            """
        )
        conn.commit()


def migrate_loan_due_date(conn):
    if not _table_exists(conn, "loans"):
        return
    if not _column_exists(conn, "loans", "expected_return_at"):
        conn.execute(
            "ALTER TABLE loans ADD COLUMN expected_return_at TEXT"
        )
        conn.commit()


def migrate_populate_locations(conn):
    if not _table_exists(conn, "locations") or not _table_exists(conn, "books"):
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO locations (name)
        SELECT DISTINCT TRIM(b.location)
        FROM books b
        WHERE b.location IS NOT NULL AND TRIM(b.location) != ''
        """
    )
    conn.commit()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    migrate_drop_isbn_unique(conn)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    migrate_locations_table(conn)
    migrate_populate_locations(conn)
    migrate_reading_status(conn)
    migrate_loan_due_date(conn)
    conn.close()
