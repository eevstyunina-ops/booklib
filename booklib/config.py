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

THEMES = ["light", "evening", "pastel"]
VIEWS  = ["tile", "compact", "list", "shelves"]

# пастельные цвета для «корешков» книг без обложки
SPINE_COLORS = [
    ("#F5C5CD", "#E8A8B5"),  # розовый
    ("#C9DFF0", "#A8C5E0"),  # голубой
    ("#D9D9D9", "#BFBFBF"),  # серый
    ("#F0E5C9", "#D9C79A"),  # кремовый
    ("#D4E8D4", "#B5D4B5"),  # шалфей
    ("#E8D4E8", "#C9B5C9"),  # лиловый
]
