import re, zipfile, base64
from pathlib import Path
from xml.etree import ElementTree as ET

FB2_NS = "{http://www.gribuser.ru/xml/fictionbook/2.0}"

def parse_fb2(path: Path):
    try:
        with open(path, "rb") as f:
            raw = f.read()
        root = ET.fromstring(raw)
    except Exception:
        return {}
    ti = root.find(f".//{FB2_NS}title-info") or root.find(".//title-info")
    if ti is None:
        return {}
    def txt(tag):
        el = ti.find(f"{FB2_NS}{tag}") or ti.find(tag)
        return (el.text or "").strip() if el is not None and el.text else ""
    title = txt("book-title")
    authors = []
    for a in ti.findall(f"{FB2_NS}author") + ti.findall("author"):
        first = a.findtext(f"{FB2_NS}first-name") or a.findtext("first-name") or ""
        last  = a.findtext(f"{FB2_NS}last-name")  or a.findtext("last-name")  or ""
        name = (first + " " + last).strip()
        if name:
            authors.append(name)
    ann = txt("annotation")
    cover_id = None
    cp = ti.find(f"{FB2_NS}coverpage") or ti.find("coverpage")
    if cp is not None:
        img = cp.find(f"{FB2_NS}image") or cp.find("image")
        if img is not None:
            cover_id = img.get("{http://www.w3.org/1999/xlink}href") or img.get("href")
    cover_bytes = None
    if cover_id:
        cid = cover_id.lstrip("#")
        for bin_el in root.findall(f"{FB2_NS}binary") + root.findall("binary"):
            if bin_el.get("id") == cid:
                try:
                    cover_bytes = base64.b64decode(bin_el.text or "")
                except Exception:
                    cover_bytes = None
                break
    return {"title": title, "author": ", ".join(authors),
            "description": re.sub("<[^>]+>", "", ann),
            "cover_bytes": cover_bytes}

def parse_epub(path: Path):
    try:
        with zipfile.ZipFile(path) as z:
            try:
                container = z.read("META-INF/container.xml")
                croot = ET.fromstring(container)
                opf_path = croot.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile").get("full-path")
            except Exception:
                return {}
            opf = ET.fromstring(z.read(opf_path))
            ns = "{http://www.idpf.org/2007/opf}"
            dc = "{http://purl.org/dc/elements/1.1/}"
            title = opf.findtext(f".//{dc}title") or ""
            authors = [e.text for e in opf.findall(f".//{dc}creator") if e.text]
            desc = opf.findtext(f".//{dc}description") or ""
            desc = re.sub("<[^>]+>", "", desc)
            cover_bytes = None
            base = Path(opf_path).parent
            cover_id = None
            for m in opf.findall(f".//{ns}meta"):
                if m.get("name") == "cover":
                    cover_id = m.get("content")
            href = None
            for item in opf.findall(f".//{ns}item"):
                if item.get("id") == cover_id or "cover-image" in (item.get("properties") or ""):
                    href = item.get("href")
                    break
            if href:
                try:
                    cover_bytes = z.read(str(base / href).replace("\\", "/"))
                except KeyError:
                    try:
                        cover_bytes = z.read(href)
                    except KeyError:
                        cover_bytes = None
            return {"title": title.strip(), "author": ", ".join(authors),
                    "description": desc.strip(), "cover_bytes": cover_bytes}
    except Exception:
        return {}

def parse_mobi(path: Path):
    """MOBI и AZW3 — извлекаем через библиотеку mobi, дальше как EPUB."""
    try:
        import mobi
        tempdir, filepath = mobi.extract(str(path))
        extracted = Path(filepath)
        if extracted.suffix.lower() == ".epub":
            return parse_epub(extracted)
        elif extracted.suffix.lower() in (".html", ".htm"):
            # Простой парс HTML — вытаскиваем <title> и автора из <meta>
            txt = extracted.read_text(errors="ignore")
            title = ""
            author = ""
            m = re.search(r"<title[^>]*>(.+?)</title>", txt, re.I | re.S)
            if m:
                title = re.sub(r"\s+", " ", m.group(1)).strip()
            m = re.search(r'<meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']+)', txt, re.I)
            if m:
                author = m.group(1).strip()
            return {"title": title, "author": author, "description": ""}
    except Exception:
        pass
    return {}

def parse_pdf(path: Path):
    result = {"title": "", "author": "", "description": ""}
    try:
        from pypdf import PdfReader
        r = PdfReader(str(path))
        meta = r.metadata or {}
        result["title"]  = (meta.get("/Title")  or "").strip()
        result["author"] = (meta.get("/Author") or "").strip()
        result["description"] = (meta.get("/Subject") or "").strip()

        if not result["title"] or not result["author"]:
            try:
                first = r.pages[0].extract_text() or ""
            except Exception:
                first = ""
            lines = [l.strip() for l in first.split("\n") if l.strip()]
            cleaned = [l for l in lines if len(l) > 2 and not re.match(r"^(Page|Стр|Страница|\d+)[\s\d]*$", l)]
            if not result["title"] and cleaned:
                for cand in cleaned[:3]:
                    if 5 < len(cand) < 150:
                        result["title"] = cand
                        break
            if not result["author"] and len(cleaned) > 1:
                for cand in cleaned[1:6]:
                    if 4 < len(cand) < 60 and not any(c.isdigit() for c in cand):
                        if not re.search(r"(издатель|press|book|library|www\.|http)", cand, re.I):
                            result["author"] = cand
                            break
    except Exception:
        pass
    return result

# -------- извлечение из имени файла и папок --------
NAME_PATTERNS = [
    re.compile(r"^(?P<author>[^—\-–]+?)\s*[—–-]\s*(?P<title>.+)$"),
    re.compile(r"^(?P<author>[^—\-–]+?)\s*\.\s+(?P<title>.+)$"),
]

def _parse_name(name: str):
    """Пытается разобрать строку 'Автор - Название' или 'Автор. Название'."""
    name = name.replace("_", " ").strip()
    for p in NAME_PATTERNS:
        m = p.match(name)
        if m:
            a = m.group("author").strip()
            t = m.group("title").strip()
            # Отбрасываем «ложных авторов»: слишком длинные, с цифрами, со словами типа «видео»
            if 2 < len(a) < 60 and not re.search(r"[0-9]{3,}", a) and not re.search(r"(видео|уроки|лекц|курс)", a, re.I):
                return {"author": a, "title": t}
    return {}

def from_filename(path: Path):
    """
    Извлекает название/автора из имени файла и родительских папок.
    Сначала пробуем имя файла, потом — названия папок.
    """
    stem = path.stem
    if stem.lower().endswith(".fb2"):
        stem = stem[:-4]
    stem = stem.replace("_", " ").strip()

    # 1. имя файла
    parsed = _parse_name(stem)
    if parsed.get("author") and parsed.get("title"):
        return parsed

    # 2. если в имени файла только название — берём как title
    title_from_file = parsed.get("title") or stem

    # 3. смотрим родительские папки на предмет "Автор - Название"
    for parent in path.parents:
        if not parent.name or parent == parent.parent:
            break
        # пропускаем очевидно системные папки
        if parent.name in ("VIDEO", "ВИДЕО", "BOOKS", "КНИГИ"):
            continue
        p2 = _parse_name(parent.name)
        if p2.get("author"):
            return {"author": p2["author"], "title": title_from_file or p2.get("title", "")}

    return {"title": title_from_file, "author": ""}

def extract(path: Path):
    ext = path.suffix.lower()
    if ext == ".fb2":
        data = parse_fb2(path)
    elif ext == ".epub":
        data = parse_epub(path)
    elif ext in (".mobi", ".azw3"):
        data = parse_mobi(path)
    elif ext == ".pdf":
        data = parse_pdf(path)
    else:
        data = {}

    if not data.get("title") or not data.get("author"):
        fb = from_filename(path)
        data["title"]  = data.get("title")  or fb["title"]
        data["author"] = data.get("author") or fb["author"]

    data["needs_review"] = 0 if (data.get("title") and data.get("author")) else 1
    return data
