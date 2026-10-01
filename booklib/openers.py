import sys, subprocess, os
from pathlib import Path

def open_file(path: str):
    p = Path(path)
    if not p.exists():
        return False
    if sys.platform.startswith("win"):
        os.startfile(str(p))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)])
    return True

def reveal(path: str):
    p = Path(path)
    if not p.exists():
        return False
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", str(p)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p.parent)])
    return True
