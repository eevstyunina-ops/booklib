import urllib.parse
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory, abort
from . import db, scanner, enrich, openers
from .config import COVERS_DIR, SEARCH_LINKS

def create_app():
    app = Flask(__name__, template_folder="templates")
    db.init()

    @app.route("/")
    def index():
        q = request.args.get("q", "").strip()
        status = request.args.get("status", "")
        sort = request.args.get("sort", "title")
        order = "ASC" if sort in ("title", "author", "added_at") else "DESC"
        sql = "SELECT * FROM books WHERE missing=0"
        params = []
        if q:
            sql += " AND (title LIKE ? OR author LIKE ?)"
            params += [f"%{q}%", f"%{q}%"]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += f" ORDER BY {sort} {order}"
        with db.connect() as c:
            books = [dict(r) for r in c.execute(sql, params)]
        return render_template("index.html", books=books, q=q, status=status,
                               sort=sort, roots=db.roots(),
                               scan=scanner.status(), enrich=enrich.status())

    @app.route("/scan", methods=["POST"])
    def scan():
        scanner.scan_async()
        return redirect(url_for("index"))

    @app.route("/scan/status")
    def scan_status():
        return jsonify({"scan": scanner.status(), "enrich": enrich.status()})

    @app.route("/enrich", methods=["POST"])
    def do_enrich():
        enrich.enrich_async(reset=False)
        return redirect(url_for("index"))

    @app.route("/enrich/reset", methods=["POST"])
    def enrich_reset():
        enrich.enrich_async(reset=True)
        return redirect(url_for("index"))

    @app.route("/roots/add", methods=["POST"])
    def add_root():
        p = request.form.get("path", "").strip()
        if p:
            db.add_root(p)
        return redirect(url_for("index"))

    @app.route("/roots/remove", methods=["POST"])
    def rm_root():
        db.remove_root(request.form.get("path", ""))
        return redirect(url_for("index"))

    @app.route("/book/<int:bid>")
    def card(bid):
        with db.connect() as c:
            row = c.execute("SELECT * FROM books WHERE id=?", (bid,)).fetchone()
            if not row:
                abort(404)
            book = dict(row)
            tags = [r["name"] for r in c.execute(
                "SELECT t.name FROM tags t JOIN book_tags bt ON bt.tag_id=t.id WHERE bt.book_id=?", (bid,))]
            shelves = [r["name"] for r in c.execute(
                "SELECT s.name FROM shelves s JOIN book_shelves bs ON bs.shelf_id=s.id WHERE bs.book_id=?", (bid,))]
        q = urllib.parse.quote(f"{book['title']} {book['author']}".strip())
        links = [(label, url.format(q=q)) for label, url in SEARCH_LINKS]
        return render_template("card.html", book=book, tags=tags, shelves=shelves, links=links)

    @app.route("/book/<int:bid>/update", methods=["POST"])
    def update(bid):
        f = request.form
        with db.connect() as c:
            c.execute("""UPDATE books SET title=?, author=?, description=?, status=?,
                                              rating=?, notes=?, needs_review=0,
                                              updated_at=CURRENT_TIMESTAMP
                         WHERE id=?""",
                      (f.get("title", ""), f.get("author", ""), f.get("description", ""),
                       f.get("status", "unread"),
                       int(f["rating"]) if f.get("rating") else None,
                       f.get("notes", ""), bid))
        _set_tags(bid, f.get("tags", ""))
        _set_shelves(bid, f.get("shelves", ""))
        return redirect(url_for("card", bid=bid))

    @app.route("/book/<int:bid>/cover", methods=["POST"])
    def upload_cover(bid):
        f = request.files.get("cover")
        if f and f.filename:
            name = f"manual_{bid}.jpg"
            try:
                from PIL import Image
                img = Image.open(f.stream).convert("RGB")
                img.thumbnail((600, 900))
                img.save(COVERS_DIR / name, "JPEG", quality=85)
            except Exception:
                f.stream.seek(0)
                (COVERS_DIR / name).write_bytes(f.read())
            with db.connect() as c:
                c.execute("UPDATE books SET cover=? WHERE id=?", (name, bid))
        return redirect(url_for("card", bid=bid))

    @app.route("/book/<int:bid>/cover/clear", methods=["POST"])
    def clear_cover(bid):
        with db.connect() as c:
            c.execute("UPDATE books SET cover=NULL WHERE id=?", (bid,))
        return redirect(url_for("card", bid=bid))

    @app.route("/book/<int:bid>/open", methods=["POST"])
    def open_book(bid):
        with db.connect() as c:
            row = c.execute("SELECT path FROM books WHERE id=?", (bid,)).fetchone()
        if row:
            openers.open_file(row["path"])
        return ("", 204)

    @app.route("/book/<int:bid>/reveal", methods=["POST"])
    def reveal_book(bid):
        with db.connect() as c:
            row = c.execute("SELECT path FROM books WHERE id=?", (bid,)).fetchone()
        if row:
            openers.reveal(row["path"])
        return ("", 204)

    @app.route("/cover/<name>")
    def cover(name):
        return send_from_directory(COVERS_DIR, name)

    @app.route("/missing")
    def missing():
        with db.connect() as c:
            books = [dict(r) for r in c.execute("SELECT * FROM books WHERE missing=1")]
        return render_template("index.html", books=books, q="", status="", sort="title",
                               roots=db.roots(), scan=scanner.status(),
                               enrich=enrich.status(), missing_view=True)

    @app.route("/missing/purge", methods=["POST"])
    def purge():
        ids = request.form.getlist("ids")
        with db.connect() as c:
            for i in ids:
                c.execute("DELETE FROM books WHERE id=?", (i,))
        return redirect(url_for("missing"))

    return app

def _set_tags(bid, raw):
    names = [t.strip() for t in raw.split(",") if t.strip()]
    with db.connect() as c:
        c.execute("DELETE FROM book_tags WHERE book_id=?", (bid,))
        for n in names:
            c.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (n,))
            tid = c.execute("SELECT id FROM tags WHERE name=?", (n,)).fetchone()["id"]
            c.execute("INSERT OR IGNORE INTO book_tags(book_id, tag_id) VALUES(?,?)", (bid, tid))

def _set_shelves(bid, raw):
    names = [t.strip() for t in raw.split(",") if t.strip()]
    with db.connect() as c:
        c.execute("DELETE FROM book_shelves WHERE book_id=?", (bid,))
        for n in names:
            c.execute("INSERT OR IGNORE INTO shelves(name) VALUES(?)", (n,))
            sid = c.execute("SELECT id FROM shelves WHERE name=?", (n,)).fetchone()["id"]
            c.execute("INSERT OR IGNORE INTO book_shelves(book_id, shelf_id) VALUES(?,?)", (bid, sid))
