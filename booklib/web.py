import urllib.parse
from flask import (Flask, render_template, request, redirect, url_for,
                   jsonify, send_from_directory, abort, make_response, Response)
from . import db, scanner, enrich, openers, ai
from .config import COVERS_DIR, SEARCH_LINKS, THEMES, VIEWS, SPINE_COLORS, APP_DIR

def create_app():
    app = Flask(__name__, template_folder="templates")
    db.init()

    def _ui_state():
        theme = request.cookies.get("theme", "light")
        view  = request.cookies.get("view", "tile")
        if theme not in THEMES: theme = "light"
        if view  not in VIEWS:  view  = "tile"
        return theme, view

    @app.context_processor
    def inject_globals():
        theme, view = _ui_state()
        return dict(
            theme=theme, view=view,
            all_tags=db.all_tags(), all_shelves=db.all_shelves(),
            all_formats=db.all_formats(),
            spine_colors=SPINE_COLORS,
        )

    @app.route("/prefs", methods=["POST"])
    def set_prefs():
        theme = request.form.get("theme")
        view  = request.form.get("view")
        resp = make_response(redirect(request.form.get("back") or url_for("index")))
        if theme in THEMES: resp.set_cookie("theme", theme, max_age=60*60*24*365)
        if view  in VIEWS:  resp.set_cookie("view",  view,  max_age=60*60*24*365)
        return resp

    @app.route("/")
    def index():
        q        = request.args.get("q", "").strip()
        status   = request.args.get("status", "")
        fmt      = request.args.get("fmt", "")
        shelf    = request.args.get("shelf", "")
        tag      = request.args.get("tag", "")
        sort     = request.args.get("sort", "title")
        order    = "ASC" if sort in ("title", "author", "added_at") else "DESC"
        sql = "SELECT DISTINCT b.* FROM books b"
        joins, where, params = [], ["b.missing=0"], []
        if shelf:
            joins.append("JOIN book_shelves bs ON bs.book_id=b.id JOIN shelves s ON s.id=bs.shelf_id")
            where.append("s.name=?"); params.append(shelf)
        if tag:
            joins.append("JOIN book_tags bt ON bt.book_id=b.id JOIN tags t ON t.id=bt.tag_id")
            where.append("t.name=?"); params.append(tag)
        if q:
            where.append("(b.title LIKE ? OR b.author LIKE ?)"); params += [f"%{q}%", f"%{q}%"]
        if status:
            where.append("b.status=?"); params.append(status)
        if fmt:
            where.append("b.fmt=?"); params.append(fmt)
        sql += " " + " ".join(joins)
        sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY b.{sort} {order}"
        with db.connect() as c:
            books = [dict(r) for r in c.execute(sql, params)]
        shelves_with_books = []
        if request.cookies.get("view") == "shelves":
            with db.connect() as c:
                for s in c.execute("SELECT id, name FROM shelves ORDER BY name"):
                    bs = [dict(r) for r in c.execute("""
                        SELECT b.* FROM books b
                        JOIN book_shelves bs ON bs.book_id=b.id
                        JOIN shelves s ON s.id=bs.shelf_id
                        WHERE s.id=? AND b.missing=0 ORDER BY b.title""", (s["id"],))]
                    shelves_with_books.append({"name": s["name"], "books": bs})
                unshelved = [dict(r) for r in c.execute("""
                    SELECT b.* FROM books b
                    WHERE b.missing=0 AND b.id NOT IN (SELECT book_id FROM book_shelves)
                    ORDER BY b.title LIMIT 200""")]
                shelves_with_books.append({"name": "Без полки", "books": unshelved, "unshelved": True})
        return render_template("index.html", books=books, q=q, status=status, fmt=fmt,
                               shelf=shelf, tag=tag, sort=sort, roots=db.roots(),
                               scan=scanner.status(), enrich=enrich.status(),
                               shelves_with_books=shelves_with_books)

    @app.route("/scan", methods=["POST"])
    def scan():
        scanner.scan_async()
        return redirect(request.referrer or url_for("index"))

    @app.route("/scan/status")
    def scan_status():
        return jsonify({"scan": scanner.status(), "enrich": enrich.status()})

    @app.route("/enrich", methods=["POST"])
    def do_enrich():
        enrich.enrich_async(reset=False)
        return redirect(request.referrer or url_for("index"))

    @app.route("/enrich/reset", methods=["POST"])
    def enrich_reset():
        enrich.enrich_async(reset=True)
        return redirect(request.referrer or url_for("index"))

    @app.route("/roots/add", methods=["POST"])
    def add_root():
        p = request.form.get("path", "").strip()
        if p: db.add_root(p)
        return redirect(request.referrer or url_for("index"))

    @app.route("/roots/remove", methods=["POST"])
    def rm_root():
        db.remove_root(request.form.get("path", ""))
        return redirect(request.referrer or url_for("index"))

    @app.route("/book/<int:bid>")
    def card(bid):
        with db.connect() as c:
            row = c.execute("SELECT * FROM books WHERE id=?", (bid,)).fetchone()
            if not row: abort(404)
            book = dict(row)
            tags = [r["name"] for r in c.execute(
                "SELECT t.name FROM tags t JOIN book_tags bt ON bt.tag_id=t.id WHERE bt.book_id=?", (bid,))]
            shelves = [r["name"] for r in c.execute(
                "SELECT s.name FROM shelves s JOIN book_shelves bs ON bs.shelf_id=s.id WHERE bs.book_id=?", (bid,))]
        q = urllib.parse.quote(f"{book['title']} {book['author']}".strip())
        links = [(label, url.format(q=q)) for label, url in SEARCH_LINKS]
        s = db.get_settings()
        return render_template("card.html", book=book, tags=tags, shelves=shelves, links=links,
                               cover_msg=request.args.get("cover_msg"),
                               ai_engine=s.get("ai_engine", "off"))

    @app.route("/book/<int:bid>/update", methods=["POST"])
    def update(bid):
        f = request.form
        with db.connect() as c:
            c.execute("""UPDATE books SET title=?, author=?, description=?, status=?,
                rating=?, notes=?, needs_review=0, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (f.get("title",""), f.get("author",""), f.get("description",""),
                 f.get("status","unread"),
                 int(f["rating"]) if f.get("rating") else None,
                 f.get("notes",""), bid))
        _set_tags(bid, f.get("tags", ""))
        _set_shelves(bid, f.get("shelves", ""))
        return redirect(url_for("card", bid=bid))

    @app.route("/book/<int:bid>/cover/upload", methods=["POST"])
    def upload_cover(bid):
        f = request.files.get("cover")
        if not f or not f.filename:
            return redirect(url_for("card", bid=bid, cover_msg="Файл не выбран"))
        name = f"manual_{bid}.jpg"
        try:
            from PIL import Image
            img = Image.open(f.stream).convert("RGB")
            img.thumbnail((600, 900))
            img.save(COVERS_DIR / name, "JPEG", quality=88)
        except Exception as e:
            return redirect(url_for("card", bid=bid, cover_msg=f"Ошибка: {e}"))
        with db.connect() as c:
            c.execute("UPDATE books SET cover=? WHERE id=?", (name, bid))
        return redirect(url_for("card", bid=bid, cover_msg="Обложка загружена"))

    @app.route("/book/<int:bid>/cover/search", methods=["POST"])
    def search_cover(bid):
        with db.connect() as c:
            row = c.execute("SELECT title, author FROM books WHERE id=?", (bid,)).fetchone()
        if not row: abort(404)
        name = enrich.fetch_cover_for(bid, row["title"] or "", row["author"] or "")
        if name:
            with db.connect() as c:
                c.execute("UPDATE books SET cover=? WHERE id=?", (name, bid))
            return redirect(url_for("card", bid=bid, cover_msg="Обложка найдена"))
        return redirect(url_for("card", bid=bid, cover_msg="В интернете ничего не нашлось"))

    @app.route("/book/<int:bid>/cover/clear", methods=["POST"])
    def clear_cover(bid):
        with db.connect() as c:
            c.execute("UPDATE books SET cover=NULL WHERE id=?", (bid,))
        return redirect(url_for("card", bid=bid, cover_msg="Обложка удалена"))

    @app.route("/book/<int:bid>/open", methods=["POST"])
    def open_book(bid):
        with db.connect() as c:
            row = c.execute("SELECT path FROM books WHERE id=?", (bid,)).fetchone()
        if row: openers.open_file(row["path"])
        return ("", 204)

    @app.route("/book/<int:bid>/reveal", methods=["POST"])
    def reveal_book(bid):
        with db.connect() as c:
            row = c.execute("SELECT path FROM books WHERE id=?", (bid,)).fetchone()
        if row: openers.reveal(row["path"])
        return ("", 204)

    @app.route("/book/<int:bid>/summarize", methods=["POST"])
    def summarize(bid):
        ai.summarize_async(bid)
        return jsonify({"ok": True})

    @app.route("/book/<int:bid>/summary_status")
    def summary_status(bid):
        st = ai.state_for(bid)
        with db.connect() as c:
            row = c.execute("SELECT summary, summary_engine, summary_at FROM books WHERE id=?", (bid,)).fetchone()
        return jsonify({
            "running": st["running"], "error": st["error"],
            "summary": row["summary"] if row else None,
            "engine": row["summary_engine"] if row else None,
            "at": row["summary_at"] if row else None,
        })

    @app.route("/book/<int:bid>/summary/clear", methods=["POST"])
    def clear_summary(bid):
        with db.connect() as c:
            c.execute("UPDATE books SET summary=NULL, summary_engine=NULL, summary_at=NULL WHERE id=?", (bid,))
        return redirect(url_for("card", bid=bid))

    @app.route("/cover/<name>")
    def cover(name):
        return send_from_directory(COVERS_DIR, name)

    @app.route("/background.jpg")
    def background_image():
        bg = APP_DIR / "background.jpg"
        if bg.exists():
            resp = send_from_directory(APP_DIR, "background.jpg")
            resp.headers["Cache-Control"] = "public, max-age=3600"
            return resp
        transparent = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
                       b'\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00'
                       b'\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01\x00'
                       b'\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
        return Response(transparent, mimetype="image/png")

    @app.route("/missing")
    def missing():
        with db.connect() as c:
            books = [dict(r) for r in c.execute("SELECT * FROM books WHERE missing=1")]
        return render_template("index.html", books=books, q="", status="", fmt="", shelf="", tag="",
                               sort="title", roots=db.roots(), scan=scanner.status(),
                               enrich=enrich.status(), missing_view=True, shelves_with_books=[])

    @app.route("/missing/purge", methods=["POST"])
    def purge():
        ids = request.form.getlist("ids")
        with db.connect() as c:
            for i in ids: c.execute("DELETE FROM books WHERE id=?", (i,))
        return redirect(url_for("missing"))

    @app.route("/duplicates")
    def duplicates():
        groups = db.find_duplicates()
        return render_template("duplicates.html", groups=groups)

    @app.route("/duplicates/clean", methods=["POST"])
    def duplicates_clean():
        groups = db.find_duplicates()
        removed = 0
        with db.connect() as c:
            for g in groups:
                for b in g["remove"]:
                    c.execute("DELETE FROM books WHERE id=?", (b["id"],))
                    removed += 1
        return redirect(url_for("duplicates", cleaned=removed))

    @app.route("/shelf/move", methods=["POST"])
    def shelf_move():
        data = request.get_json(force=True)
        bid = int(data.get("book_id"))
        shelf = (data.get("shelf") or "").strip()
        if not shelf: return jsonify({"ok": False, "err": "no shelf"})
        with db.connect() as c:
            c.execute("INSERT OR IGNORE INTO shelves(name) VALUES(?)", (shelf,))
            sid = c.execute("SELECT id FROM shelves WHERE name=?", (shelf,)).fetchone()["id"]
            c.execute("DELETE FROM book_shelves WHERE book_id=?", (bid,))
            if shelf != "Без полки":
                c.execute("INSERT OR IGNORE INTO book_shelves(book_id, shelf_id) VALUES(?,?)", (bid, sid))
        return jsonify({"ok": True})

    # ============ НАСТРОЙКИ ============
    @app.route("/settings")
    def settings():
        s = db.get_settings()
        defaults = {
            "voice_lang": "ru-RU", "ai_engine": "off",
            "ollama_url": "http://localhost:11434", "ollama_model": "llama3.1:8b",
            "openai_key": "", "openai_model": "gpt-4o-mini",
            "anthropic_key": "", "anthropic_model": "claude-3-5-haiku-20241022",
        }
        for k, v in defaults.items():
            if k not in s or s[k] is None:
                s[k] = v
        return render_template("settings.html", settings=s, test_result=None)

    @app.route("/settings/save", methods=["POST"])
    def settings_save():
        data = {
            "voice_lang":      request.form.get("voice_lang", "ru-RU"),
            "ai_engine":       request.form.get("ai_engine", "off"),
            "ollama_url":      request.form.get("ollama_url", "").strip(),
            "ollama_model":    request.form.get("ollama_model", "").strip(),
            "openai_key":      request.form.get("openai_key", "").strip(),
            "openai_model":    request.form.get("openai_model", "").strip(),
            "anthropic_key":   request.form.get("anthropic_key", "").strip(),
            "anthropic_model": request.form.get("anthropic_model", "").strip(),
        }
        db.save_settings(data)
        return redirect(url_for("settings", saved=1))

    @app.route("/settings/test", methods=["POST"])
    def settings_test():
        ok, msg = ai.test_engine()
        s = db.get_settings()
        return render_template("settings.html", settings=s, test_result={"ok": ok, "msg": msg})

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
