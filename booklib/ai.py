"""ИИ: Ollama (локально) + OpenAI + Anthropic."""
import threading
from datetime import datetime
import requests
from . import db
from .text_extract import extract as extract_text

_states = {}
_lock = threading.Lock()

def state_for(book_id):
    with _lock:
        return dict(_states.get(book_id, {"running": False, "done": False, "error": None}))

def summarize_async(book_id):
    with _lock:
        if _states.get(book_id, {}).get("running"):
            return False
        _states[book_id] = {"running": True, "done": False, "error": None}
    threading.Thread(target=_worker, args=(book_id,), daemon=True).start()
    return True

def _worker(book_id):
    try:
        with db.connect() as c:
            row = c.execute("SELECT title, author, path, fmt FROM books WHERE id=?", (book_id,)).fetchone()
        if not row:
            _fail(book_id, "Книга не найдена в базе"); return
        s = db.get_settings()
        engine = s.get("ai_engine", "off")
        if engine == "off":
            _fail(book_id, "ИИ выключен. Откройте Настройки и выберите движок."); return
        text = extract_text(row["path"], max_chars=15000)
        if not text or len(text) < 200:
            _fail(book_id, "Не удалось прочитать текст (возможно, это скан без текстового слоя)."); return
        prompt = _build_prompt(row["title"] or "", row["author"] or "", text)
        summary = _ask(prompt, s)
        if not summary:
            _fail(book_id, "ИИ не вернул ответ"); return
        with db.connect() as c:
            c.execute("UPDATE books SET summary=?, summary_engine=?, summary_at=? WHERE id=?",
                      (summary, engine, datetime.now().isoformat(timespec="seconds"), book_id))
        with _lock:
            _states[book_id] = {"running": False, "done": True, "error": None}
    except Exception as e:
        _fail(book_id, str(e))

def _fail(book_id, msg):
    with _lock:
        _states[book_id] = {"running": False, "done": False, "error": msg}

def _build_prompt(title, author, text):
    return f"""Ты — помощник в домашней библиотеке. Тебе дан текст из разных частей книги: начало, середина и конец (места стыков помечены [...]). Составь краткое содержание ВСЕЙ книги целиком, а не отдельной главы.

Название: {title or '(без названия)'}
Автор: {author or '(неизвестен)'}

Фрагменты книги:
\"\"\"
{text}
\"\"\"

Опирайся на все три фрагмента. Если в начале одна тема, а в середине или конце другая — упомяни всё. Не пересказывай отдельную главу — давай общее представление о книге.

Структура ответа:
1. **О чём книга** — 3–4 предложения о всей книге.
2. **Ключевые идеи** — 5–7 тезисов о книге в целом.
3. **Кому будет интересно** — одна фраза.

Пиши живо, без воды, не выдумывай факты."""

def _ask(prompt, s):
    engine = s.get("ai_engine", "off")
    if engine == "ollama":
        return _ask_ollama(prompt, s.get("ollama_url", "http://localhost:11434"), s.get("ollama_model", "llama3.1:8b"))
    if engine == "openai":
        return _ask_openai(prompt, s.get("openai_key", ""), s.get("openai_model", "gpt-4o-mini"))
    if engine == "anthropic":
        return _ask_anthropic(prompt, s.get("anthropic_key", ""), s.get("anthropic_model", "claude-3-5-haiku-20241022"))
    return None

def _ask_ollama(prompt, url, model):
    r = requests.post(f"{url.rstrip('/')}/api/generate", json={
        "model": model, "prompt": prompt, "stream": False,
        "options": {"temperature": 0.3, "num_predict": 1200}
    }, timeout=900)
    r.raise_for_status()
    return (r.json().get("response") or "").strip()

def _ask_openai(prompt, key, model):
    if not key: raise RuntimeError("Не задан OpenAI API-ключ (Настройки → ИИ)")
    r = requests.post("https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.3, "max_tokens": 1500}, timeout=180)
    if r.status_code == 401: raise RuntimeError("OpenAI: неверный ключ (401)")
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def _ask_anthropic(prompt, key, model):
    if not key: raise RuntimeError("Не задан Anthropic API-ключ (Настройки → ИИ)")
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": model, "max_tokens": 2000, "messages": [{"role": "user", "content": prompt}]},
        timeout=180)
    if r.status_code == 401: raise RuntimeError("Anthropic: неверный ключ (401)")
    r.raise_for_status()
    return r.json()["content"][0]["text"].strip()

def test_engine():
    s = db.get_settings()
    engine = s.get("ai_engine", "off")
    if engine == "off":
        return False, "ИИ выключен"
    try:
        if engine == "ollama":
            url = s.get("ollama_url", "http://localhost:11434").rstrip("/")
            r = requests.get(f"{url}/api/tags", timeout=8)
            if r.ok:
                models = [m.get("name") for m in r.json().get("models", [])]
                return True, f"Ollama отвечает. Модели: {', '.join(models[:5]) or 'нет'}"
            return False, f"Ollama ответила кодом {r.status_code}"
        if engine == "openai":
            ans = _ask_openai("Ответь одним словом: работает?", s.get("openai_key",""), s.get("openai_model","gpt-4o-mini"))
            return True, f"OpenAI ответил: {ans[:80]}"
        if engine == "anthropic":
            ans = _ask_anthropic("Ответь одним словом: работает?", s.get("anthropic_key",""), s.get("anthropic_model","claude-3-5-haiku-20241022"))
            return True, f"Anthropic ответил: {ans[:80]}"
    except requests.exceptions.ConnectionError:
        return False, "Не удалось соединиться. Проверьте, что Ollama запущена."
    except Exception as e:
        return False, str(e)
    return False, "Неизвестная ошибка"
