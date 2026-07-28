"""Local web interface (Flask). Runs on localhost only, so secrets never leave the machine.

Stage 1 serves the interface shell and a health check. Stage 2 adds read endpoints
(drafts, reference, queue). Stage 3a adds the generation-flow actions (ingest, brainstorm,
select, skeleton) so the browser can drive the pipeline the CLI already implements.
"""

import functools
import webbrowser
from datetime import datetime
from pathlib import Path

from flask import Flask, request, send_from_directory

from . import agent_log, config, drafts, processing, state

WEBUI_DIR = Path(__file__).resolve().parent / "webui"


def guarded(view):
    """Turn a pipeline SystemExit (missing setting, bad JSON, API overload) into a clean
    JSON error instead of a 500 that would kill the dev server. Unexpected errors surface
    as a 500 payload with their message, and still print in the terminal for debugging.
    """

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except SystemExit as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:  # noqa: BLE001 - local tool, show the message rather than crash
            return {"error": f"Unexpected error: {exc}"}, 500

    return wrapper


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

    # --- Stage 3a: generation-flow actions ---

    @app.post("/api/ingest")
    @guarded
    def api_ingest():
        """Rebuild reference.md from GitHub activity and notes since the last post."""
        since, _agent_log, reference = agent_log.run_ingest()
        return {
            "ok": True,
            "since": since.isoformat() if since else None,
            "reference_chars": len(reference),
        }

    @app.post("/api/brainstorm")
    @guarded
    def api_brainstorm():
        """Brainstorm ideas from the reference. Pass reshuffle:true to avoid the prior set."""
        data = request.get_json(silent=True) or {}
        count = int(data.get("count") or config.BRAINSTORM_IDEA_COUNT)
        reference = processing.load_reference()
        avoid = state.get_processing().get("ideas") if data.get("reshuffle") else None
        ideas = processing.brainstorm_ideas(reference, count, avoid=avoid or None)
        state.save_processing({"ideas": ideas, "selected_index": None, "comments": ""})
        return {"ideas": ideas, "selected_index": None}

    @app.get("/api/ideas")
    def api_ideas():
        session = state.get_processing()
        return {
            "ideas": session.get("ideas") or [],
            "selected_index": session.get("selected_index"),
            "comments": session.get("comments", ""),
        }

    @app.post("/api/select")
    @guarded
    def api_select():
        """Record which idea the owner picked, plus optional direction for the skeleton."""
        data = request.get_json(silent=True) or {}
        session = state.get_processing()
        ideas = session.get("ideas") or []
        index = int(data.get("index", -1))
        if index < 0 or index >= len(ideas):
            return {"error": "No idea at that position. Brainstorm first."}, 400
        session["selected_index"] = index
        session["comments"] = (data.get("comment") or "").strip()
        state.save_processing(session)
        return {"ok": True, "selected": ideas[index]}

    @app.post("/api/skeleton")
    @guarded
    def api_skeleton():
        """Write a skeleton draft (with [YOUR VOICE: ...] gaps) from the selected idea."""
        session = state.get_processing()
        ideas = session.get("ideas") or []
        selected = session.get("selected_index")
        if selected is None:
            return {"error": "Select an idea before creating a skeleton."}, 400
        idea = ideas[selected]
        reference = processing.load_reference()
        body = processing.write_skeleton(reference, idea, session.get("comments", ""))

        draft_id = drafts.new_draft_id()
        drafts.write_draft(
            {
                "id": draft_id,
                "status": "skeleton",
                "flow": "stage_b",
                "created": datetime.now().isoformat(timespec="seconds"),
                "idea_title": idea.get("title", ""),
            },
            body,
            drafts.draft_path(draft_id),
        )
        return {"id": draft_id, "body": body}

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
