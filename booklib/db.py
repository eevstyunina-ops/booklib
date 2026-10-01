import sqlite3
from contextlib import contextmanager
from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id           INTEGER PRIMARY KEY,
    fingerprint  TEXT UNIQUE NOT NULL,
    path         TEXT NOT NULL,
    size         INTEGER NOT NULL,
    mtime        REAL,
    fmt          TEXT,
    title        TEXT,
    author       TEXT,
    description  TEXT,
    cover        TEXT,
    status       TEXT DEFAULT 'unread',
    rating       INTEGER,
    notes        TEXT,
    needs_review INTEGER DEFAULT 0,
    missing      INTEGER DEFAULT 0,
    enriched     INTEGER DEFAULT 0,
    added_at     TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS book_tags (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
    PRIMARY KEY (book_id, tag_id)
);

CREATE TABLE IF NOT EXISTS shelves (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS book_shelves (
    book_id   INTEGER NOT NULL REFERENCES books(id)   ON DELETE CASCADE,
    shelf_id  INTEGER NOT NULL REFERENCES shelves(id) ON DELETE CASCADE,
    PRIMARY KEY (book_id, shelf_id)
);

CREATE TABLE IF NOT EXISTS roots (
    id   INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS enrich_cache (
    query TEXT PRIMARY KEY,
    payload TEXT,
    ts TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_books_missing ON books(missing);
CREATE INDEX IF NOT EXISTS idx_books_title   ON books(title);
CREATE INDEX IF NOT EXISTS idx_books_author  ON books(author);
"""

def init():
    with connect() as c:
        c.executescript(SCHEMA)

@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def add_root(path: str):
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO roots(path) VALUES(?)", (path,))

def roots():
    with connect() as c:
        return [r["path"] for r in c.execute("SELECT path FROM roots")]

def remove_root(path: str):
    with connect() as c:
        c.execute("DELETE FROM roots WHERE path=?", (path,))
