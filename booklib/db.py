import sqlite3
from contextlib import contextmanager
from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id            INTEGER PRIMARY KEY,
    fingerprint   TEXT UNIQUE NOT NULL,
    path          TEXT NOT NULL,
    size          INTEGER NOT NULL,
    mtime         REAL,
    fmt           TEXT,
    title         TEXT,
    author        TEXT,
    description   TEXT,
    cover         TEXT,
    status        TEXT DEFAULT 'unread',
    rating        INTEGER,
    notes         TEXT,
    needs_review  INTEGER DEFAULT 0,
    missing       INTEGER DEFAULT 0,
    enriched      INTEGER DEFAULT 0,
    cover_checked INTEGER DEFAULT 0,
    added_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at    TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS book_tags (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
    PRIMARY KEY (book_id, tag_id)
);
CREATE TABLE IF NOT EXISTS shelves (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS book_shelves (
    book_id   INTEGER NOT NULL REFERENCES books(id)   ON DELETE CASCADE,
    shelf_id  INTEGER NOT NULL REFERENCES shelves(id) ON DELETE CASCADE,
    PRIMARY KEY (book_id, shelf_id)
);
CREATE TABLE IF NOT EXISTS roots (
    id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS enrich_cache (
    query TEXT PRIMARY KEY, payload TEXT, ts TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_books_missing ON books(missing);
CREATE INDEX IF NOT EXISTS idx_books_title   ON books(title);
CREATE INDEX IF NOT EXISTS idx_books_author  ON books(author);
"""

def _migrate(conn):
    for sql in ["ALTER TABLE books ADD COLUMN cover_checked INTEGER DEFAULT 0"]:
        try: conn.execute(sql)
        except sqlite3.OperationalError: pass

def init():
    with connect() as c:
        c.executescript(SCHEMA)
        _migrate(c)
        try:
            c.execute("INSERT OR IGNORE INTO settings(id) VALUES(1)")
        except Exception:
            pass

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
    with connect() as c: c.execute("INSERT OR IGNORE INTO roots(path) VALUES(?)", (path,))
def roots():
    with connect() as c: return [r["path"] for r in c.execute("SELECT path FROM roots")]
def remove_root(path: str):
    with connect() as c: c.execute("DELETE FROM roots WHERE path=?", (path,))

def all_tags():
    with connect() as c:
        return [dict(r) for r in c.execute("""SELECT t.id, t.name, COUNT(bt.book_id) AS n
                                              FROM tags t LEFT JOIN book_tags bt ON bt.tag_id=t.id
                                              GROUP BY t.id ORDER BY t.name""")]

def all_shelves():
    with connect() as c:
        return [dict(r) for r in c.execute("""SELECT s.id, s.name, COUNT(bs.book_id) AS n
                                              FROM shelves s LEFT JOIN book_shelves bs ON bs.shelf_id=s.id
                                              GROUP BY s.id ORDER BY s.name""")]

def all_formats():
    with connect() as c:
        return [dict(r) for r in c.execute("""SELECT fmt, COUNT(*) AS n FROM books
                                              WHERE missing=0 AND fmt IS NOT NULL AND fmt!=''
                                              GROUP BY fmt ORDER BY n DESC""")]

def score_book(b):
    s = 0
    if b.get("cover"): s += 10
    if b.get("rating"): s += 3
    if b.get("notes"): s += 2
    if b.get("description"): s += 1
    return s

def find_duplicates():
    with connect() as c:
        rows = [dict(r) for r in c.execute("""
            SELECT id, title, author, cover, rating, notes, description, size, path, fmt
            FROM books WHERE missing=0 AND title IS NOT NULL AND title != ''
            ORDER BY id""")]
    groups = {}
    for r in rows:
        key = (r["title"].strip().lower(), (r["author"] or "").strip().lower())
        if key[0]:
            groups.setdefault(key, []).append(r)
    result = []
    for key, items in groups.items():
        if len(items) > 1:
            items.sort(key=score_book, reverse=True)
            result.append({"key": key, "books": items, "keep": items[0], "remove": items[1:]})
    return result
