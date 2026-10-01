"""Извлечение текста из книг для ИИ."""
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

def extract(path, max_chars: int = 15000) -> str:
    p = Path(path)
    ext = p.suffix.lower()
    try:
        if ext == ".fb2":   return _from_fb2(p, max_chars)
        if ext == ".epub":  return _from_epub(p, max_chars)
        if ext == ".pdf":   return _from_pdf(p, max_chars)
        if ext in (".mobi", ".azw3"): return _from_mobi(p, max_chars)
    except Exception as e:
        return f"[ошибка извлечения: {e}]"
    return ""

def _from_fb2(path, max_chars):
    with open(path, "rb") as f:
        raw = f.read()
    root = ET.fromstring(raw)
    body = root.find(f"{FB2_NS}body") or root.find("body")
    if body is None: return ""
    parts, total = [], 0
    for el in body.iter():
        if el.tag.endswith("}p") or el.tag == "p":
            if el.text and el.text.strip():
                parts.append(el.text.strip())
                total += len(el.text)
                if total >= max_chars: break
    return "\n\n".join(parts)[:max_chars]

def _from_epub(path, max_chars):
    with zipfile.ZipFile(path) as z:
        html_files = [n for n in z.namelist() if n.lower().endswith((".html", ".xhtml", ".htm"))]
        html_files.sort()
        parts, total = [], 0
        for hf in html_files:
            try:
                raw = z.read(hf).decode("utf-8", errors="ignore")
            except Exception:
                continue
            t = _strip_html(raw)
            if t:
                parts.append(t)
                total += len(t)
                if total >= max_chars: break
        return "\n\n".join(parts)[:max_chars]

def _from_pdf(path, max_chars):
    from pypdf import PdfReader
    r = PdfReader(str(path))
    parts, total = [], 0
    for i, page in enumerate(r.pages):
        if i < 3: continue
        try:
            t = page.extract_text() or ""
        except Exception:
            continue
        if t.strip():
            parts.append(t.strip())
            total += len(t)
            if total >= max_chars: break
        if i > 60: break
    return "\n\n".join(parts)[:max_chars]

def _from_mobi(path, max_chars):
    try:
        import mobi
        tempdir, filepath = mobi.extract(str(path))
        extracted = Path(filepath)
        if extracted.suffix.lower() == ".epub":
            return _from_epub(extracted, max_chars)
        if extracted.suffix.lower() in (".html", ".htm"):
            return _strip_html(extracted.read_text(errors="ignore"))[:max_chars]
    except Exception:
        pass
    return ""
