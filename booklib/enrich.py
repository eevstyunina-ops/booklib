import json, re, time, threading
import requests
from .config import ENRICH_DELAY, OFFLINE, COVERS_DIR
from . import db

_state = {"running": False, "done": 0, "total": 0, "mode": "", "current": ""}

STOPWORDS = {"и","в","на","с","по","для","от","до","из","к","о","у","за","под",
             "the","a","an","of","and","to","in","on","for"}

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

# ---------- проверки ----------

def _is_garbage_title(title: str) -> bool:
    """Отсеиваем «мусорные» названия, по которым нет смысла искать."""
    t = (title or "").strip()
    if len(t) < 4:
        return True
    if t.isdigit():
        return True
    # что-то вида "-ASS~1", "03 27136"
    if re.match(r"^[^A-Za-zА-Яа-яЁё]+$", t):
        return True
    if re.match(r"^\d+\s+\w{1,6}$", t):
        return True
    if re.fullmatch(r"[A-Za-z]{1,3}", t):
        return True
    return False

def _tokens(s: str):
    return set(re.findall(r"[A-Za-zА-Яа-яЁё0-9]{2,}", (s or "").lower())) - STOPWORDS

def _title_matches(query: str, found: str) -> bool:
    """Проверяет, что найденное название хоть как-то совпадает с запросом."""
    qt = _tokens(query)
    ft = _tokens(found)
    if not qt or not ft:
        return False
    common = qt & ft
    # Если хотя бы одно значимое слово совпало, или одно слово — подстрока другого
    if common:
        return True
    for q in qt:
        for f in ft:
            if len(q) >= 4 and (q in f or f in q):
                return True
    return False

def _expand_title(title: str):
    """Дополнительные варианты названия: 7 → Семь, 3 → Три."""
    mapping = {"7": "Семь", "3": "Три", "5": "Пять", "100": "Сто", "2": "Два"}
    m = re.match(r"^(\d+)\s+(.+)$", title)
    if m:
        num, rest = m.group(1), m.group(2)
        if num in mapping:
            yield mapping[num] + " " + rest
    yield title

# ---------- поиск обложки ----------

def fetch_cover_for(book_id, title, author):
    if _is_garbage_title(title):
        return None

    queries = []
    title_variants = list(_expand_title(title))
    for tv in title_variants:
        if author:
            queries.append(f"{tv} {author}".strip())
            queries.append(f"{author} {tv}".strip())
        queries.append(tv)
    # дополнительный вариант без лишних слов
    short = re.split(r"[:(]", title)[0].strip()
    if short and short != title:
        if author:
            queries.append(f"{short} {author}".strip())
        queries.append(short)

    queries = list(dict.fromkeys(q for q in queries if q))

    # 1. ЛитРес — для русских книг в первую очередь
    for q in queries:
        name = _from_litres(book_id, q, title)
        if name:
            return name

    # 2. Open Library
    for q in queries:
        name = _from_openlibrary(book_id, q, title)
        if name:
            return name

    # 3. Google Books
    for q in queries:
        name = _from_google(book_id, q, title)
        if name:
            return name

    return None

def _from_litres(book_id, query, original_title):
    """ЛитРес — русские книги с качественными обложками."""
    try:
        url = "https://www.litres.ru/search/"
        r = requests.get(url, params={"q": query}, timeout=12,
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        if not r.ok:
            return None
        html = r.text
        # Ищем блоки с обложками и названиями
        # у ЛитРес обложки лежат на cdn.litres.ru/pub/c/cover_200/...
        pattern = re.compile(
            r'<img[^>]+src="(https://cdn\.litres\.ru/pub/c/[^"]+?\.(?:jpg|jpeg|png))"[^>]*>',
            re.I
        )
        # Также пытаемся вытащить названия для проверки
        title_pattern = re.compile(r'<a[^>]+class="[^"]*art__title[^"]*"[^>]*>([^<]+)</a>', re.I)

        covers = pattern.findall(html)
        titles = [re.sub(r"\s+", " ", t).strip() for t in title_pattern.findall(html)]

        for i, cover_url in enumerate(covers[:6]):
            found_title = titles[i] if i < len(titles) else original_title
            if _title_matches(original_title, found_title):
                name = _download_cover(book_id, cover_url)
                if name:
                    return name
    except Exception:
        pass
    return None

def _from_openlibrary(book_id, query, original_title):
    try:
        r = requests.get("https://openlibrary.org/search.json",
                         params={"q": query, "limit": 5, "fields": "cover_i,title,author_name"},
                         timeout=10)
        if r.ok:
            for d in r.json().get("docs", []):
                cid = d.get("cover_i")
                found_title = d.get("title", "")
                if cid and _title_matches(original_title, found_title):
                    name = _download_cover(book_id, f"https://covers.openlibrary.org/b/id/{cid}-L.jpg")
                    if name:
                        return name
    except Exception:
        pass
    return None

def _from_google(book_id, query, original_title):
    try:
        r = requests.get("https://www.googleapis.com/books/v1/volumes",
                         params={"q": query, "maxResults": 8}, timeout=10)
        if not r.ok:
            return None
        for item in r.json().get("items", []):
            vi = item.get("volumeInfo", {})
            found_title = vi.get("title", "")
            if not _title_matches(original_title, found_title):
                continue
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

# ---------- описание ----------

def _cache_get(q):
    with db.connect() as c:
        row = c.execute("SELECT payload FROM enrich_cache WHERE query=?", (q,)).fetchone()
        return json.loads(row["payload"]) if row else None

def _cache_put(q, payload):
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO enrich_cache(query, payload) VALUES(?,?)",
                  (q, json.dumps(payload, ensure_ascii=False)))

def _lookup(title, author):
    if _is_garbage_title(title):
        return {}
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
                if _title_matches(title, vi.get("title", "")):
                    result = {"description": vi.get("description", ""),
                              "title": vi.get("title"),
                              "authors": vi.get("authors")}
    except Exception:
        pass
    _cache_put(q, result)
    return result
