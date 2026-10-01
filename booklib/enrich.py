import json, time, threading
import requests
from .config import ENRICH_DELAY, OFFLINE, COVERS_DIR
from . import db

_state = {"running": False, "done": 0, "total": 0, "mode": ""}

def status():
    return dict(_state)

def enrich_async(reset=False):
    if _state["running"] or OFFLINE:
        return False
    if reset:
        with db.connect() as c:
            c.execute("UPDATE books SET enriched=0, cover_checked=0")
    _state.update(running=True, done=0, total=0, mode="enrich")
    threading.Thread(target=_run, daemon=True).start()
    return True

def _run():
    try:
        with db.connect() as c:
            rows = c.execute("""SELECT id, title, author, cover FROM books
                                WHERE missing=0 AND title != '' AND needs_review=0
                                  AND (enriched=0 OR (cover IS NULL AND cover_checked=0))""").fetchall()
        _state["total"] = len(rows)
        for r in rows:
            try:
                info = _lookup(r["title"], r["author"])
                cover_name = None
                if info and info.get("cover_url") and not r["cover"]:
                    cover_name = _download_cover(r["id"], info["cover_url"])
                with db.connect() as c:
                    if info:
                        c.execute("""UPDATE books
                                     SET description=COALESCE(NULLIF(description,''), ?),
                                         cover=COALESCE(cover, ?),
                                         enriched=1, cover_checked=1
                                     WHERE id=?""",
                                  (info.get("description", ""), cover_name, r["id"]))
                    else:
                        c.execute("UPDATE books SET enriched=1, cover_checked=1 WHERE id=?", (r["id"],))
            except Exception:
                pass
            _state["done"] += 1
            time.sleep(ENRICH_DELAY)
    finally:
        _state["running"] = False

def _download_cover(book_id, url):
    try:
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        r = requests.get(url, timeout=15)
        if not r.ok or len(r.content) < 500:
            return None
        name = f"enrich_{book_id}.jpg"
        (COVERS_DIR / name).write_bytes(r.content)
        return name
    except Exception:
        return None

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
                imgs = vi.get("imageLinks") or {}
                cover_url = (imgs.get("extraLarge") or imgs.get("large")
                             or imgs.get("medium") or imgs.get("thumbnail")
                             or imgs.get("smallThumbnail"))
                result = {"description": vi.get("description", ""),
                          "title": vi.get("title"),
                          "authors": vi.get("authors"),
                          "cover_url": cover_url}
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
                    cid = d.get("cover_i")
                    result = {"title": d.get("title"),
                              "authors": d.get("author_name"),
                              "cover_url": f"https://covers.openlibrary.org/b/id/{cid}-L.jpg" if cid else None}
        except Exception:
            pass
    _cache_put(q, result)
    return result
