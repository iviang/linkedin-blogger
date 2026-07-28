"""Local web interface (Flask). Runs on localhost only, so secrets never leave the machine.

Stage 1 serves the interface shell and a health check. Later stages add read endpoints
(drafts, reference, queue) and action endpoints (ingest, brainstorm, check, publish) over
the existing pipeline.
"""

import webbrowser
from pathlib import Path

from flask import Flask, send_from_directory

WEBUI_DIR = Path(__file__).resolve().parent / "webui"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)

    @app.get("/")
    def index():
        return send_from_directory(WEBUI_DIR, "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


def run(host: str = "127.0.0.1", port: int = 5000, open_browser: bool = True) -> None:
    """Start the local server. Binds to 127.0.0.1 so nothing is exposed to the network."""
    app = create_app()
    url = f"http://{host}:{port}"
    print(f"LinkedIn Blogger running at {url}")
    print("Local only: nothing is exposed to the internet. Stop with Ctrl+C.")
    if open_browser:
        webbrowser.open(url)
    app.run(host=host, port=port, debug=False)
