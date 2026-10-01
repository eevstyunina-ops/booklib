import json, time, threading
import requests
from .config import ENRICH_DELAY, OFFLINE, COVERS_DIR
from . import db

_state = {"running": False, "done": 0, "total": 0, "mode": "", "current": ""}

def status():
    return dict(_state)

def enrich_async(reset=False):
    if _state["running"] or OFFLINE:
        return False
    if reset:
        with db.connect() as c:
            c.execute("UPDATE books SET enriched=0, cover_checked=0")
    _state.update(running=True, done=0, total=0, mode="enrich", current="")
    threading.Thread(target=_run, daemon=True).start()
    return True

def _run():
    try:
        # ВАЖНО: берём и книги «требует проверки» — если есть хоть какое-то название, ищем обложку.
        with db.connect() as c:
            rows = c.execute("""SELECT id, title, author, cover FROM books
                                WHERE missing=0
                                  AND title IS NOT NULL AND title != ''
                                  AND (enriched=0 OR (cover IS NULL AND cover_checked=0))""").fetchall()
        _state["total"] = len(rows)
        for r in rows:
            _state["current"] = f"{r['title']}"
            try:
                info = _lookup(r["title"], r["author"])
                cover_name = None
                if not r["cover"]:
                    if info and info.get("cover_url"):
                        cover_name = _download_cover(r["id"], info["cover_url"])
                    if not cover_name:
                        cover_name = fetch_cover_for(r["id"], r["title"], r["author"] or "")
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
        _state["current"] = ""

def _download_cover(book_id, url):
    try:
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        if "books.google" in url:
            url = url.split("&edge=")[0].split("&zoom=")[0]
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if not r.ok or len(r.content) < 800:
            return None
        name = f"enrich_{book_id}.jpg"
        try:
            from PIL import Image
            from io import BytesIO
            img = Image.open(BytesIO(r.content)).convert("RGB")
            img.thumbnail((600, 900))
            img.save(COVERS_DIR / name, "JPEG", quality=88)
        except Exception:
            (COVERS_DIR / name).write_bytes(r.content)
        return name
    except Exception:
        return None

def fetch_cover_for(book_id, title, author):
    if not title:
        return None
    # Убираем шум из «названий» типа «03 27136»
    clean_title = title.strip()
    if len(clean_title) < 3 or all(c.isdigit() or c.isspace() for c in clean_title):
        return None
    queries = []
    if author:
        queries.append(f"{clean_title} {author}".strip())
        queries.append(f"{author} {clean_title}".strip())
    queries.append(clean_title)
    # Open Library
    for q in queries:
        try:
            r = requests.get("https://openlibrary.org/search.json",
                             params={"q": q, "limit": 5, "fields": "cover_i,title,author_name"},
                             timeout=10)
            if r.ok:
                for d in r.json().get("docs", []):
                    cid = d.get("cover_i")
                    if cid:
                        name = _download_cover(book_id, f"https://covers.openlibrary.org/b/id/{cid}-L.jpg")
                        if name:
                            return name
        except Exception:
            pass
    # Google Books
    for q in queries:
        try:
            r = requests.get("https://www.googleapis.com/books/v1/volumes",
                             params={"q": q, "maxResults": 5}, timeout=10)
            if r.ok:
                for item in r.json().get("items", []):
                    vi = item.get("volumeInfo", {})
                    imgs = vi.get("imageLinks") or {}
                    url = (imgs.get("extraLarge") or imgs.get("large")
                           or imgs.get("medium") or imgs.get("thumbnail"))
                    if url:
                        name = _download_cover(book_id, url)
                        if name:
                            return name
        except Exception:
            pass
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
                             or imgs.get("medium") or imgs.get("thumbnail"))
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
