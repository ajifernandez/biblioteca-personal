import os
import sqlite3
from flask import g

DB_PATH = os.environ.get("DB_PATH", "/data/biblioteca.db")
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "schema.sql")


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


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    migrate_drop_isbn_unique(conn)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()
