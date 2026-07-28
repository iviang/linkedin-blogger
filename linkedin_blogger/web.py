"""Local web interface (Flask). Runs on localhost only, so secrets never leave the machine.

Stage 1 serves the interface shell and a health check. Stage 2 adds read endpoints
(drafts, reference, queue). Stage 3a adds the generation-flow actions (ingest, brainstorm,
select, skeleton) so the browser can drive the pipeline the CLI already implements.
"""

import functools
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from . import agent_log, auth, config, drafts, processing, queue, state

WEBUI_DIR = Path(__file__).resolve().parent / "webui"
UPLOADS_DIR = config.DRAFTS_DIR / "uploads"  # under gitignored drafts/, so photos never get committed
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif"}

# Two lanes on the dashboard. Once a draft is queued it leaves the Drafts list and lives in
# the Queue; posted/posting drafts are done and show in neither.
QUEUE_STATUSES = {"queued", "approved", "failed"}
DONE_STATUSES = {"posted", "posting"}

# Cached once per server run: the picture URL from userinfo can expire, but that is fine for
# a local tool restarted often, and it avoids a LinkedIn call on every preview.
_PROFILE_CACHE: dict = {}


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
        in_drafts = [d for d in items if d["status"] not in QUEUE_STATUSES | DONE_STATUSES]
        queued = [d for d in items if d["status"] in QUEUE_STATUSES]
        return {
            "last_posted_at": last.isoformat() if last else None,
            "reference_exists": config.REFERENCE_FILE.exists(),
            "repos": config.GITHUB_REPOS,
            "draft_count": len(in_drafts),
            "queued_count": len(queued),
        }

    @app.get("/api/drafts")
    def api_drafts():
        # Only work-in-progress drafts; queued ones move to the Queue, posted ones are done.
        items = [d for d in drafts.list_drafts() if d["status"] not in QUEUE_STATUSES | DONE_STATUSES]
        return {"drafts": items}

    @app.get("/api/queue")
    def api_queue():
        queued = [d for d in drafts.list_drafts() if d["status"] in QUEUE_STATUSES]
        return {"queue": queued}

    @app.get("/api/profile")
    def api_profile():
        """Your LinkedIn name and picture for the preview. Falls back to nulls if not logged in."""
        if _PROFILE_CACHE.get("name"):
            return _PROFILE_CACHE
        try:
            profile = auth.get_profile()
        except Exception:  # noqa: BLE001 - not logged in / network; preview falls back gracefully
            return {"name": None, "picture": None}
        if profile.get("name"):
            _PROFILE_CACHE.update(profile)
        return profile

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

    # --- Stage 3b: edit a draft, run its check, override flags ---

    def _draft_detail(draft_id: str) -> dict | None:
        """Full editor payload for one draft: body, status, check, and edit-lock state."""
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return None
        meta, body = drafts.read_draft(path)
        try:
            queue.assert_editable(meta)
            editable, lock_reason = True, ""
        except SystemExit as exc:
            editable, lock_reason = False, str(exc)
        return {
            "id": meta.get("id", draft_id),
            "status": meta.get("status", "?"),
            "title": meta.get("idea_title", ""),
            "body": body,
            "scheduled_at": meta.get("scheduled_at"),
            "media": meta.get("media"),
            "media_alt": meta.get("media_alt", ""),
            "check": processing.load_check(draft_id),
            "check_stale": processing.check_is_stale(draft_id, body),
            "has_gaps": processing.has_unfilled_gaps(body),
            "editable": editable,
            "lock_reason": lock_reason,
        }

    @app.get("/api/drafts/<draft_id>")
    def api_draft_get(draft_id):
        detail = _draft_detail(draft_id)
        if detail is None:
            return {"error": "No draft with that id."}, 404
        return detail

    @app.post("/api/drafts/<draft_id>")
    @guarded
    def api_draft_save(draft_id):
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return {"error": "No draft with that id."}, 404
        meta, _body = drafts.read_draft(path)
        queue.assert_editable(meta)  # locked/posted -> SystemExit -> guarded 400
        data = request.get_json(silent=True) or {}
        new_body = data.get("body")
        if new_body is None:
            return {"error": "Missing body."}, 400
        if "title" in data:
            meta["idea_title"] = (data.get("title") or "").strip()
        if "media_alt" in data and meta.get("media"):
            meta["media_alt"] = (data.get("media_alt") or "").strip()
        # A body edit invalidates any prior check; check_is_stale surfaces that until re-run.
        drafts.write_draft(meta, new_body, path)
        return _draft_detail(draft_id)

    @app.post("/api/drafts/<draft_id>/check")
    @guarded
    def api_draft_check(draft_id):
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return {"error": "No draft with that id."}, 404
        meta, body = drafts.read_draft(path)
        if meta.get("status") in ("posted", "posting"):
            return {"error": "That draft is already posted."}, 400
        reference = processing.load_reference()
        result = processing.run_check(reference, body)
        processing.save_check(draft_id, result)
        if result["passed"] and meta.get("status") == "skeleton":
            meta["status"] = "pending"
            drafts.write_draft(meta, body, path)
        return _draft_detail(draft_id)

    @app.post("/api/drafts/<draft_id>/override")
    @guarded
    def api_draft_override(draft_id):
        data = request.get_json(silent=True) or {}
        flag_id = data.get("flag_id")
        if not flag_id:
            return {"error": "Missing flag_id."}, 400
        check = processing.apply_override(draft_id, flag_id, data.get("reason") or "")
        if check.get("passed"):
            path = drafts.draft_path(draft_id)
            meta, body = drafts.read_draft(path)
            if meta.get("status") == "skeleton":
                meta["status"] = "pending"
                drafts.write_draft(meta, body, path)
        return _draft_detail(draft_id)

    @app.delete("/api/drafts/<draft_id>")
    @guarded
    def api_draft_delete(draft_id):
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return {"error": "No draft with that id."}, 404
        meta, _body = drafts.read_draft(path)
        if meta.get("status") in ("posted", "posting"):
            return {"error": "A posted draft is the record of that post and cannot be deleted here."}, 400
        drafts.delete_draft(draft_id)
        return {"ok": True}

    @app.post("/api/drafts/<draft_id>/schedule")
    @guarded
    def api_draft_schedule(draft_id):
        """Queue or reschedule a draft for a given time. Mirrors the CLI approve/schedule
        rules: a stage_b draft must pass its check before its first scheduling."""
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return {"error": "No draft with that id."}, 404
        data = request.get_json(silent=True) or {}
        when = data.get("scheduled_at")

        meta, body = drafts.read_draft(path)
        status = meta.get("status")
        if status in ("posted", "posting"):
            return {"error": f"Draft is {status} and cannot be scheduled."}, 400
        if status == "skeleton":
            return {"error": "Fill the gaps and run check before scheduling."}, 400
        queue.assert_editable(meta)  # locked -> SystemExit -> guarded 400

        # First-time scheduling of a checked draft must actually be ready; rescheduling one
        # that is already queued/approved/failed just moves the time.
        if status not in ("queued", "approved", "failed") and meta.get("flow") == "stage_b":
            if processing.check_is_stale(draft_id, body):
                return {"error": "Draft changed since its last check. Run check again first."}, 400
            ready, message = processing.check_ready_for_approve(draft_id)
            if not ready:
                return {"error": message}, 400

        # No time given means "space it after the queue": land it one interval after the
        # latest already-scheduled post (or the last live post), else schedule for now.
        scheduled = queue.parse_scheduled_at(when) if when else _default_schedule(draft_id)
        meta.update(queue.queue_meta(scheduled))
        drafts.write_draft(meta, body, path)
        return _draft_detail(draft_id)

    def _default_schedule(exclude_id: str) -> datetime:
        interval = timedelta(days=state.get_post_interval_days())
        anchors = []
        for item in drafts.list_drafts():
            if item["id"] == exclude_id:
                continue
            if item["status"] in ("queued", "approved") and item.get("scheduled_at"):
                anchors.append(queue.parse_scheduled_at(item["scheduled_at"]))
        last_posted = state.get_last_posted_at()
        if last_posted:
            anchors.append(last_posted)
        if anchors:
            return max(anchors) + interval
        return datetime.now().astimezone()

    @app.get("/api/settings")
    def api_settings_get():
        return {"post_interval_days": state.get_post_interval_days()}

    @app.post("/api/settings")
    @guarded
    def api_settings_save():
        data = request.get_json(silent=True) or {}
        try:
            days = int(data.get("post_interval_days"))
        except (TypeError, ValueError):
            return {"error": "Enter a whole number of days."}, 400
        if days < 1 or days > 365:
            return {"error": "Posting frequency must be between 1 and 365 days."}, 400
        state.set_post_interval_days(days)
        return {"post_interval_days": days}

    # --- Stage 3c: media, and publishing ---

    @app.post("/api/drafts/<draft_id>/media")
    @guarded
    def api_draft_media_upload(draft_id):
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return {"error": "No draft with that id."}, 404
        meta, body = drafts.read_draft(path)
        queue.assert_editable(meta)
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return {"error": "No file selected."}, 400
        ext = Path(upload.filename).suffix.lower()
        if ext not in ALLOWED_IMAGE_EXTS:
            return {"error": f"Unsupported image type {ext or '(none)'}. Use JPG, PNG, or GIF."}, 400
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        safe = secure_filename(upload.filename) or f"image{ext}"
        dest = UPLOADS_DIR / f"{draft_id}-{safe}"
        upload.save(dest)
        meta["media"] = dest.relative_to(config.BASE_DIR).as_posix()
        alt = (request.form.get("alt") or "").strip()
        if alt:
            meta["media_alt"] = alt
        drafts.write_draft(meta, body, path)
        return _draft_detail(draft_id)

    @app.delete("/api/drafts/<draft_id>/media")
    @guarded
    def api_draft_media_remove(draft_id):
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return {"error": "No draft with that id."}, 404
        meta, body = drafts.read_draft(path)
        queue.assert_editable(meta)
        meta.pop("media", None)
        meta.pop("media_alt", None)
        drafts.write_draft(meta, body, path)
        return _draft_detail(draft_id)

    @app.get("/api/drafts/<draft_id>/media")
    def api_draft_media_get(draft_id):
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return {"error": "No draft with that id."}, 404
        meta, _body = drafts.read_draft(path)
        raw = meta.get("media")
        if not raw:
            return {"error": "No media on this draft."}, 404
        abspath = (config.BASE_DIR / raw).resolve()
        base = config.BASE_DIR.resolve()
        # Only ever serve files under the project root, never an arbitrary path from meta.
        if base not in abspath.parents or not abspath.exists():
            return {"error": "Media file missing."}, 404
        return send_file(abspath)

    def _publish_one(draft_id: str, path):
        """Shared publish for a single draft file. Returns (ok, error_or_None)."""
        meta, body = drafts.read_draft(path)

        def write_draft(updated_meta, updated_body):
            drafts.write_draft(updated_meta, updated_body, path)

        ok = queue.publish_draft(meta, body, write_draft)
        if ok:
            return True, None
        after, _ = drafts.read_draft(path)
        return False, after.get("publish_error", "Publish failed.")

    @app.post("/api/drafts/<draft_id>/publish")
    @guarded
    def api_draft_publish(draft_id):
        """Publish one draft right now, bypassing its scheduled time (a deliberate post-now)."""
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return {"error": "No draft with that id."}, 404
        meta, body = drafts.read_draft(path)
        status = meta.get("status")
        if status in ("posted", "posting"):
            return {"error": f"Draft is already {status}."}, 400
        if status == "skeleton":
            return {"error": "Fill the gaps and run check before publishing."}, 400
        if meta.get("flow") == "stage_b":
            if processing.check_is_stale(draft_id, body):
                return {"error": "Draft changed since its last check. Run check again first."}, 400
            ready, message = processing.check_ready_for_approve(draft_id)
            if not ready:
                return {"error": message}, 400
        ok, error = _publish_one(draft_id, path)
        return {"ok": ok, "error": error, "draft": _draft_detail(draft_id)}

    @app.post("/api/publish")
    @guarded
    def api_publish_due():
        """Publish every draft whose scheduled time has arrived. Mirrors `blogger.py publish`."""
        published, failed, results = 0, 0, []
        for path in sorted(config.DRAFTS_DIR.glob("*.md")):
            meta, _body = drafts.read_draft(path)
            if not queue.ready_to_publish(meta):
                continue
            draft_id = meta.get("id", path.stem)
            ok, error = _publish_one(draft_id, path)
            results.append({"id": draft_id, "ok": ok, "error": error})
            published += 1 if ok else 0
            failed += 0 if ok else 1
        return {"published": published, "failed": failed, "results": results}

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
