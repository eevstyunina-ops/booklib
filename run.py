import argparse, webbrowser, threading
from booklib.web import create_app

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8756)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    app = create_app()
    url = f"http://127.0.0.1:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Открывайте: {url}")
    app.run(host="127.0.0.1", port=args.port, debug=False)

if __name__ == "__main__":
    main()
