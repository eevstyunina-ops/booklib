import json, time, threading
import requests
from .config import ENRICH_DELAY, OFFLINE
from . import db

_state = {"running": False, "done": 0, "total": 0}

def status():
    return dict(_state)

def enrich_async():
    if _state["running"] or OFFLINE:
        return False
    _state.update(running=True, done=0, total=0)
    threading.Thread(target=_run, daemon=True).start()
    return True

def _run():
    try:
        with db.connect() as c:
            rows = c.execute("""SELECT id, title, author FROM books
                                WHERE enriched=0 AND missing=0
                                  AND title != '' AND needs_review=0""").fetchall()
        _state["total"] = len(rows)
        for r in rows:
            try:
                info = _lookup(r["title"], r["author"])
                if info:
                    with db.connect() as c:
                        c.execute("""UPDATE books SET description=COALESCE(NULLIF(description,''), ?),
                                                       enriched=1 WHERE id=?""",
                                  (info.get("description", ""), r["id"]))
                else:
                    with db.connect() as c:
                        c.execute("UPDATE books SET enriched=1 WHERE id=?", (r["id"],))
            except Exception:
                pass
            _state["done"] += 1
            time.sleep(ENRICH_DELAY)
    finally:
        _state["running"] = False

def _cache_get(q):
    with db.connect() as c:
        row = c.execute("SELECT payload FROM enrich_cache WHERE query=?", (q,)).fetchone()
        return json.loads(row["payload"]) if row else None

def _cache_put(q, payload):
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO enrich_cache(query, payload) VALUES(?,?)",
                  (q, json.dumps(payload, ensure_ascii=False)))

def _lookup(title, author):
    q = f"{title} {author}".strip()
    cached = _cache_get(q)
    if cached is not None:
        return cached
    result = {}
    try:
        r = requests.get("https://www.googleapis.com/books/v1/volumes",
                         params={"q": q, "maxResults": 1}, timeout=10)
        if r.ok:
            items = r.json().get("items") or []
            if items:
                vi = items[0]["volumeInfo"]
                result = {"description": vi.get("description", ""),
                          "title": vi.get("title"),
                          "authors": vi.get("authors")}
    except Exception:
        pass
    if not result:
        try:
            r = requests.get("https://openlibrary.org/search.json",
                             params={"q": q, "limit": 1}, timeout=10)
            if r.ok:
                docs = r.json().get("docs") or []
                if docs:
                    d = docs[0]
                    result = {"title": d.get("title"),
                              "authors": d.get("author_name")}
        except Exception:
            pass
    _cache_put(q, result)
    return result
