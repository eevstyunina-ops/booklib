"""Извлечение текста из книг для ИИ — берём фрагменты из начала, середины и конца."""
import re, zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

FB2_NS = "{http://www.gribuser.ru/xml/fictionbook/2.0}"

def _strip_html(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S|re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def _sample(full_text: str, max_chars: int) -> str:
    """Берём кусочки с начала, середины и конца."""
    n = len(full_text)
    if n <= max_chars:
        return full_text
    chunk = max_chars // 3
    head = full_text[:chunk]
    mid_start = n // 2 - chunk // 2
    mid = full_text[mid_start:mid_start + chunk]
    tail = full_text[-chunk:]
    return (head + "\n\n[...]\n\n" + mid + "\n\n[...]\n\n" + tail)

def extract(path, max_chars: int = 30000) -> str:
    p = Path(path)
    ext = p.suffix.lower()
    try:
        if ext == ".fb2":   return _sample(_from_fb2(p), max_chars)
        if ext == ".epub":  return _sample(_from_epub(p), max_chars)
        if ext == ".pdf":   return _sample(_from_pdf(p), max_chars)
        if ext in (".mobi", ".azw3"): return _sample(_from_mobi(p), max_chars)
    except Exception as e:
        return f"[ошибка извлечения: {e}]"
    return ""

def _from_fb2(path):
    with open(path, "rb") as f:
        raw = f.read()
    root = ET.fromstring(raw)
    body = root.find(f"{FB2_NS}body") or root.find("body")
    if body is None: return ""
    parts = []
    for el in body.iter():
        if el.tag.endswith("}p") or el.tag == "p":
            if el.text and el.text.strip():
                parts.append(el.text.strip())
    return "\n\n".join(parts)

def _from_epub(path):
    with zipfile.ZipFile(path) as z:
        html_files = [n for n in z.namelist() if n.lower().endswith((".html", ".xhtml", ".htm"))]
        html_files.sort()
        parts = []
        for hf in html_files:
            try:
                raw = z.read(hf).decode("utf-8", errors="ignore")
            except Exception:
                continue
            t = _strip_html(raw)
            if t:
                parts.append(t)
        return "\n\n".join(parts)

def _from_pdf(path):
    from pypdf import PdfReader
    r = PdfReader(str(path))
    parts = []
    for i, page in enumerate(r.pages):
        if i < 2: continue   # пропускаем обложку и титул
        try:
            t = page.extract_text() or ""
        except Exception:
            continue
        if t.strip():
            parts.append(t.strip())
        if i > 200: break
    return "\n\n".join(parts)

def _from_mobi(path):
    try:
        import mobi
        tempdir, filepath = mobi.extract(str(path))
        extracted = Path(filepath)
        if extracted.suffix.lower() == ".epub":
            return _from_epub(extracted)
        if extracted.suffix.lower() in (".html", ".htm"):
            return _strip_html(extracted.read_text(errors="ignore"))
    except Exception:
        pass
    return ""
