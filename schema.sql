CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    isbn TEXT UNIQUE,
    title TEXT NOT NULL,
    author TEXT,
    publisher TEXT,
    published_year TEXT,
    cover_url TEXT,
    description TEXT,
    location TEXT,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reading_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('leyendo', 'leido')),
    started_at TEXT,
    finished_at TEXT,
    rating INTEGER CHECK (rating BETWEEN 1 AND 5),
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);
CREATE INDEX IF NOT EXISTS idx_books_author ON books(author);
CREATE INDEX IF NOT EXISTS idx_reading_log_book ON reading_log(book_id);
