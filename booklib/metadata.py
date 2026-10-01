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

def parse_pdf(path: Path):
    try:
        from pypdf import PdfReader
        r = PdfReader(str(path))
        meta = r.metadata or {}
        return {"title": (meta.get("/Title") or "").strip(),
                "author": (meta.get("/Author") or "").strip(),
                "description": (meta.get("/Subject") or "").strip()}
    except Exception:
        return {}

NAME_PATTERNS = [
    re.compile(r"^(?P<author>[^—\-–]+?)\s*[—–-]\s*(?P<title>.+)$"),
    re.compile(r"^(?P<author>[^—\-–]+?)\s*\.\s+(?P<title>.+)$"),
]

def from_filename(path: Path):
    stem = path.stem
    if stem.lower().endswith(".fb2"):
        stem = stem[:-4]
    stem = stem.replace("_", " ").strip()
    for p in NAME_PATTERNS:
        m = p.match(stem)
        if m:
            return {"author": m.group("author").strip(),
                    "title": m.group("title").strip()}
    return {"title": stem, "author": ""}

def extract(path: Path):
    ext = path.suffix.lower()
    if ext == ".fb2":
        data = parse_fb2(path)
    elif ext == ".epub":
        data = parse_epub(path)
    elif ext == ".pdf":
        data = parse_pdf(path)
    else:
        data = {}
    if not data.get("title") or not data.get("author"):
        fb = from_filename(path)
        data["title"] = data.get("title") or fb["title"]
        data["author"] = data.get("author") or fb["author"]
    data["needs_review"] = 0 if (data.get("title") and data.get("author")) else 1
    return data
