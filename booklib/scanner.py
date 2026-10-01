import hashlib, threading
from pathlib import Path
from .config import SUPPORTED, SUPPORTED_ZIP, COVERS_DIR
from . import db, metadata

_state = {"running": False, "done": 0, "total": 0, "current": "", "error": None}
_lock = threading.Lock()

def _is_supported(p: Path) -> bool:
    name = p.name.lower()
    if any(name.endswith(z) for z in SUPPORTED_ZIP):
        return True
    return p.suffix.lower() in SUPPORTED

def fingerprint(path: Path, chunk: int = 65536) -> str:
    size = path.stat().st_size
    h = hashlib.sha1()
    h.update(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(chunk))
        if size > chunk * 3:
            f.seek(size // 2)
            h.update(f.read(chunk))
            f.seek(-chunk, 2)
            h.update(f.read(chunk))
    return h.hexdigest()

def _save_cover(fp: str, data: bytes):
    if not data:
        return None
    name = f"{fp}.jpg"
    (COVERS_DIR / name).write_bytes(data)
    return name

def status():
    return dict(_state)

def scan_async():
    with _lock:
        if _state["running"]:
            return False
        _state.update(running=True, done=0, total=0, error=None)
    threading.Thread(target=_scan, daemon=True).start()
    return True

def _scan():
    try:
        roots = db.roots()
        files = []
        for root in roots:
            rp = Path(root)
            if not rp.exists():
                continue
            for p in rp.rglob("*"):
                if p.is_file() and _is_supported(p):
                    files.append(p)
        _state["total"] = len(files)
        seen_fps = set()
        for p in files:
            _state["current"] = str(p)
            try:
                fp = fingerprint(p)
                seen_fps.add(fp)
                _upsert(p, fp)
            except Exception as e:
                _state["error"] = f"{p}: {e}"
            _state["done"] += 1
        with db.connect() as c:
            rows = c.execute("SELECT id, fingerprint FROM books WHERE missing=0").fetchall()
            for r in rows:
                if r["fingerprint"] not in seen_fps:
                    c.execute("UPDATE books SET missing=1 WHERE id=?", (r["id"],))
    finally:
        _state["running"] = False
        _state["current"] = ""

def _upsert(path: Path, fp: str):
    with db.connect() as c:
        row = c.execute("SELECT id, path FROM books WHERE fingerprint=?", (fp,)).fetchone()
        if row:
            if row["path"] != str(path):
                c.execute("UPDATE books SET path=?, mtime=?, missing=0, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                          (str(path), path.stat().st_mtime, row["id"]))
            else:
                c.execute("UPDATE books SET missing=0 WHERE id=?", (row["id"],))
            return
    data = metadata.extract(path)
    cover_name = _save_cover(fp, data.get("cover_bytes"))
    fmt = path.suffix.lower().lstrip(".")
    with db.connect() as c:
        c.execute("""INSERT INTO books(fingerprint, path, size, mtime, fmt, title, author,
                                       description, cover, needs_review)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (fp, str(path), path.stat().st_size, path.stat().st_mtime, fmt,
                   data.get("title") or "", data.get("author") or "",
                   data.get("description") or "", cover_name,
                   data.get("needs_review", 1)))
