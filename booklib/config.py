from pathlib import Path
import os

APP_DIR = Path(os.environ.get("BOOKLIB_HOME", Path.home() / ".booklib"))
APP_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = APP_DIR / "library.sqlite"
COVERS_DIR = APP_DIR / "covers"
COVERS_DIR.mkdir(exist_ok=True)

SUPPORTED = {".fb2", ".epub", ".pdf", ".mobi", ".azw3", ".djvu"}
SUPPORTED_ZIP = {".fb2.zip"}

ENRICH_DELAY = 1.0
OFFLINE = os.environ.get("BOOKLIB_OFFLINE") == "1"

SEARCH_LINKS = [
    ("Google",   "https://www.google.com/search?q={q}"),
    ("Литрес",   "https://www.litres.ru/search/?q={q}"),
    ("Лайвлиб",  "https://www.livelib.ru/find/{q}"),
]
