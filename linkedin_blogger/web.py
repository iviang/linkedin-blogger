"""Local web interface (Flask). Runs on localhost only, so secrets never leave the machine.

Stage 1 serves the interface shell and a health check. Later stages add read endpoints
(drafts, reference, queue) and action endpoints (ingest, brainstorm, check, publish) over
the existing pipeline.
"""

import webbrowser
from pathlib import Path

from flask import Flask, send_from_directory

from . import config, drafts, state

WEBUI_DIR = Path(__file__).resolve().parent / "webui"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)

    @app.get("/")
    def index():
        return send_from_directory(WEBUI_DIR, "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/status")
    def api_status():
        last = state.get_last_posted_at()
        items = drafts.list_drafts()
        queued = [d for d in items if d["status"] in ("queued", "approved", "failed")]
        return {
            "last_posted_at": last.isoformat() if last else None,
            "reference_exists": config.REFERENCE_FILE.exists(),
            "repos": config.GITHUB_REPOS,
            "draft_count": len(items),
            "queued_count": len(queued),
        }

    @app.get("/api/drafts")
    def api_drafts():
        return {"drafts": drafts.list_drafts()}

    @app.get("/api/queue")
    def api_queue():
        queued = [d for d in drafts.list_drafts() if d["status"] in ("queued", "approved", "failed")]
        return {"queue": queued}

    @app.get("/api/reference")
    def api_reference():
        if not config.REFERENCE_FILE.exists():
            return {"exists": False, "content": ""}
        return {"exists": True, "content": config.REFERENCE_FILE.read_text(encoding="utf-8")}

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
