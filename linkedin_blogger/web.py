"""Local web interface (Flask). Runs on localhost only, so secrets never leave the machine.

Stage 1 serves the interface shell and a health check. Stage 2 adds read endpoints
(drafts, reference, queue). Stage 3a adds the generation-flow actions (ingest, brainstorm,
select, skeleton) so the browser can drive the pipeline the CLI already implements.
"""

import functools
import math
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from . import agent_log, auth, config, drafts, llm, media, processing, queue, state, versions

WEBUI_DIR = Path(__file__).resolve().parent / "webui"
UPLOADS_DIR = config.DRAFTS_DIR / "uploads"  # under gitignored drafts/, so photos never get committed
IMAGE_MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif"}

# Two lanes on the dashboard. Once a draft is queued it leaves the Drafts list and lives in
# the Queue; posted/posting drafts are done and show in neither.
QUEUE_STATUSES = {"queued", "approved", "failed"}
DONE_STATUSES = {"posted", "posting"}

# Cached once per server run: the picture URL from userinfo can expire, but that is fine for
# a local tool restarted often, and it avoids a LinkedIn call on every preview.
_PROFILE_CACHE: dict = {}

# In-memory activity feed for the terminal panel. Endpoints append short lines here; the UI
# polls for new ones. Kept small and per-run (not persisted) since it is just a live view.
_ACTIVITY: list = []
_ACTIVITY_SEQ = [0]


def _log(text: str) -> None:
    _ACTIVITY_SEQ[0] += 1
    _ACTIVITY.append({"seq": _ACTIVITY_SEQ[0], "ts": datetime.now().strftime("%H:%M:%S"), "text": text})
    if len(_ACTIVITY) > 200:
        del _ACTIVITY[: len(_ACTIVITY) - 200]


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


def _next_nudge_days() -> int | None:
    """Days until the next nudge, measured from the most recent post or nudge using the owner's
    posting interval, so it moves the moment they change the N-days setting. None before any post
    or nudge. Shared by the status and settings endpoints so both report the same figure."""
    anchors = [t for t in (state.get_last_posted_at(), state.get_last_nudged_at()) if t]
    if not anchors:
        return None
    due = max(anchors) + timedelta(days=state.get_post_interval_days())
    days = (due - datetime.now(timezone.utc)).total_seconds() / 86400
    return max(0, int(math.ceil(days)))


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

        reference_generated = None
        if config.REFERENCE_FILE.exists():
            mtime = config.REFERENCE_FILE.stat().st_mtime
            reference_generated = datetime.fromtimestamp(mtime, timezone.utc).isoformat()

        return {
            "last_posted_at": last.isoformat() if last else None,
            "reference_exists": config.REFERENCE_FILE.exists(),
            "reference_generated": reference_generated,
            "next_nudge_days": _next_nudge_days(),
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

    @app.get("/api/activity")
    def api_activity():
        after = request.args.get("after", default=0, type=int)
        return {"entries": [e for e in _ACTIVITY if e["seq"] > after], "last": _ACTIVITY_SEQ[0]}

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
        _log("ingest: gathering activity and synthesizing the reference with Claude")
        since, _agent_log, reference = agent_log.run_ingest()
        _log(f"ingest: reference rebuilt ({len(reference)} chars)")
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
        _log("brainstorm: asking Claude for " + str(count) + " post ideas")
        ideas = processing.brainstorm_ideas(reference, count, avoid=avoid or None)
        state.save_processing({"ideas": ideas, "selected_index": None, "comments": ""})
        _log(f"brainstorm: got {len(ideas)} ideas")
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
        _log("skeleton: writing a draft for \"" + idea.get("title", "") + "\" with Claude")
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
        versions.record(draft_id, "created", "Skeleton created", idea.get("title", ""), body, "auto")
        _log(f"skeleton: draft {draft_id} created")
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
        media_out = []
        for item in drafts.get_media(meta):
            file_path = config.BASE_DIR / item["path"]
            media_out.append(
                {
                    "alt": item["alt"],
                    "kind": media.kind(file_path),
                    "warnings": media.warnings_for(file_path) if file_path.exists() else [],
                }
            )
        return {
            "id": meta.get("id", draft_id),
            "status": meta.get("status", "?"),
            "title": meta.get("idea_title", ""),
            "body": body,
            "scheduled_at": meta.get("scheduled_at"),
            "media": media_out,
            "media_warnings": media.post_warnings([m["kind"] for m in media_out]),
            "check": processing.load_check(draft_id),
            "check_stale": processing.check_is_stale(draft_id, body),
            "has_gaps": processing.has_unfilled_gaps(body),
            "editable": editable,
            "lock_reason": lock_reason,
        }

    @app.post("/api/drafts/new")
    @guarded
    def api_draft_new():
        """Create a blank draft for freestyle writing (no skeleton). Returns its detail."""
        data = request.get_json(silent=True) or {}
        draft_id = drafts.new_draft_id()
        meta = {
            "id": draft_id,
            "status": "pending",
            "created": datetime.now().isoformat(timespec="seconds"),
            "idea_title": (data.get("title") or "").strip(),
        }
        body = data.get("body") or ""
        drafts.write_draft(meta, body, drafts.draft_path(draft_id))
        versions.record(draft_id, "created", "Draft created", meta["idea_title"], body, "you")
        _log(f"new: freestyle draft {draft_id} created")
        return _draft_detail(draft_id)

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
        # A body edit invalidates any prior check; check_is_stale surfaces that until re-run.
        drafts.write_draft(meta, new_body, path)
        versions.record(draft_id, "edit", "Edited draft", meta.get("idea_title", ""), new_body, "you")
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
        _log(f"check: reviewing draft {draft_id} against the reference with Claude")
        result = processing.run_check(reference, body)
        processing.save_check(draft_id, result)
        if result["passed"] and meta.get("status") == "skeleton":
            meta["status"] = "pending"
            drafts.write_draft(meta, body, path)
        open_flags = len([f for f in result.get("flags", []) if not f.get("overridden")])
        _log(f"check: {'passed' if result['passed'] else str(open_flags) + ' open flag(s)'}")
        label = "Error check passed" if result["passed"] else f"Error check: {open_flags} open flag(s)"
        versions.record(draft_id, "check", label, meta.get("idea_title", ""), body, "auto")
        return _draft_detail(draft_id)

    @app.post("/api/drafts/<draft_id>/override")
    @guarded
    def api_draft_override(draft_id):
        data = request.get_json(silent=True) or {}
        flag_id = data.get("flag_id")
        if not flag_id:
            return {"error": "Missing flag_id."}, 400
        check = processing.apply_override(draft_id, flag_id, data.get("reason") or "")
        path = drafts.draft_path(draft_id)
        meta, body = drafts.read_draft(path)
        if check.get("passed") and meta.get("status") == "skeleton":
            meta["status"] = "pending"
            drafts.write_draft(meta, body, path)
        versions.record(draft_id, "override", f"Overrode {flag_id}", meta.get("idea_title", ""), body, "you")
        _log(f"override: {flag_id} on {draft_id}")
        return _draft_detail(draft_id)

    @app.post("/api/drafts/<draft_id>/accept")
    @guarded
    def api_draft_accept(draft_id):
        """Apply a flag's suggested fix to the draft body."""
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return {"error": "No draft with that id."}, 404
        meta, body = drafts.read_draft(path)
        queue.assert_editable(meta)
        data = request.get_json(silent=True) or {}
        flag_id = data.get("flag_id")
        if not flag_id:
            return {"error": "Missing flag_id."}, 400
        new_body = processing.apply_suggestion(draft_id, flag_id, body)
        if meta.get("status") == "skeleton" and processing.check_ready_for_approve(draft_id)[0]:
            meta["status"] = "pending"
        drafts.write_draft(meta, new_body, path)
        versions.record(draft_id, "edit", f"Accepted fix for {flag_id}", meta.get("idea_title", ""), new_body, "you")
        _log(f"accept: applied suggested fix for {flag_id} on {draft_id}")
        return _draft_detail(draft_id)

    @app.post("/api/drafts/<draft_id>/trim")
    @guarded
    def api_draft_trim(draft_id):
        """Suggest a shorter rewrite for an over-length draft. Returns the trimmed text and its
        word count; the caller decides whether to apply it. Only offered when the draft is over
        the limit, since trimming a draft already within the limit would just risk its meaning."""
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return {"error": "No draft with that id."}, 404
        meta, body = drafts.read_draft(path)
        if meta.get("status") in ("posted", "posting"):
            return {"error": "That draft is already posted."}, 400
        words = len(body.split())
        if words <= config.MAX_POST_WORDS:
            return {"error": f"Draft is {words} words, within the {config.MAX_POST_WORDS}-word limit. No trim needed."}, 400
        _log(f"trim: asking Claude to tighten {draft_id} under {config.MAX_POST_WORDS} words")
        suggestion = processing.suggest_trim(body, config.MAX_POST_WORDS)
        new_words = len(suggestion.split())
        _log(f"trim: suggested {words} -> {new_words} words for {draft_id}")
        return {"suggestion": suggestion, "words": new_words, "original_words": words}

    @app.get("/api/drafts/<draft_id>/versions")
    def api_versions(draft_id):
        return {"versions": versions.summaries(draft_id)}

    @app.get("/api/drafts/<draft_id>/versions/<int:index>")
    def api_version_get(draft_id, index):
        version = versions.get(draft_id, index)
        if version is None:
            return {"error": "No such version."}, 404
        return {
            "title": version.get("title", ""),
            "body": version.get("body", ""),
            "label": version["label"],
            "at": version["at"],
            "who": version["who"],
        }

    @app.post("/api/drafts/<draft_id>/versions/<int:index>/restore")
    @guarded
    def api_version_restore(draft_id, index):
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return {"error": "No draft with that id."}, 404
        meta, _body = drafts.read_draft(path)
        queue.assert_editable(meta)
        version = versions.get(draft_id, index)
        if version is None:
            return {"error": "No such version."}, 404
        meta["idea_title"] = version.get("title", "")
        drafts.write_draft(meta, version.get("body", ""), path)
        versions.record(draft_id, "restore", "Restored an earlier version",
                        version.get("title", ""), version.get("body", ""), "you")
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
        _log(f"delete: {draft_id} moved to trash")
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

        words = len(body.split())
        if words > config.MAX_POST_WORDS:
            return {"error": f"Draft is {words} words, over the {config.MAX_POST_WORDS}-word limit. Trim it before queuing."}, 400

        # First-time scheduling of a checked draft must actually be ready; rescheduling one
        # that is already queued/approved/failed just moves the time.
        if status not in ("queued", "approved", "failed") and meta.get("flow") == "stage_b":
            if processing.check_is_stale(draft_id, body):
                return {"error": "Draft changed since its last check. Run check again first."}, 400
            ready, message = processing.check_ready_for_approve(draft_id)
            if not ready:
                return {"error": message}, 400

        # An explicit time on today must clear the same-day lead window; no time given means
        # "space it after the queue", one interval past the furthest post (or now if empty).
        if when:
            scheduled = queue.parse_scheduled_at(when)
            queue.assert_min_lead(scheduled)  # same-day-too-soon -> SystemExit -> guarded 400
        else:
            scheduled = queue.default_schedule(draft_id)
        meta.update(queue.queue_meta(scheduled))
        drafts.write_draft(meta, body, path)
        _log(f"queue: {draft_id} scheduled for {scheduled.astimezone().strftime('%Y-%m-%d %H:%M')}")
        return _draft_detail(draft_id)

    @app.post("/api/drafts/<draft_id>/unqueue")
    @guarded
    def api_draft_unqueue(draft_id):
        """Pull a queued draft back out of the queue: clear its schedule and return it to the
        drafts list as an editable draft. Always available for a queued draft, even inside the
        publish lock, because this cancels the post rather than editing its content: the lock
        exists to freeze wording before publish, not to force an about-to-post draft out."""
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return {"error": "No draft with that id."}, 404
        meta, body = drafts.read_draft(path)
        if meta.get("status") not in ("queued", "approved", "failed"):
            return {"error": "Only a queued draft can be removed from the queue."}, 400
        meta["status"] = "pending"
        meta.pop("scheduled_at", None)
        meta.pop("publish_error", None)
        meta.pop("failed_at", None)
        drafts.write_draft(meta, body, path)
        versions.record(draft_id, "unqueue", "Removed from queue", meta.get("idea_title", ""), body, "you")
        _log(f"queue: {draft_id} removed from the queue, back to drafts")
        return _draft_detail(draft_id)

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
        # Return the recomputed nudge so the UI can update it instantly, without a second call.
        return {"post_interval_days": days, "next_nudge_days": _next_nudge_days()}

    # --- Stage 3c: media, and publishing ---

    @app.post("/api/drafts/<draft_id>/media")
    @guarded
    def api_draft_media_upload(draft_id):
        """Append one or more photos to the draft. Accepts multiple files in one request."""
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return {"error": "No draft with that id."}, 404
        meta, body = drafts.read_draft(path)
        queue.assert_editable(meta)
        uploads = [u for u in request.files.getlist("file") if u and u.filename]
        if not uploads:
            return {"error": "No file selected."}, 400
        # Validate every file before saving any, so a bad one mid-batch leaves no orphans.
        for upload in uploads:
            ext = Path(upload.filename).suffix.lower()
            if ext not in media.ALLOWED_EXTS:
                return {"error": f"Unsupported file type {ext or '(none)'}. Use JPG, PNG, GIF, or MP4."}, 400
        media_list = drafts.get_media(meta)
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        for upload in uploads:
            ext = Path(upload.filename).suffix.lower()
            safe = secure_filename(upload.filename) or f"image{ext}"
            # A microsecond stamp keeps names unique so a re-added file never overwrites another.
            stamp = datetime.now().strftime("%H%M%S%f")
            dest = UPLOADS_DIR / f"{draft_id}-{stamp}-{safe}"
            upload.save(dest)
            media_list.append({"path": dest.relative_to(config.BASE_DIR).as_posix(), "alt": ""})
        drafts.set_media(meta, media_list)
        drafts.write_draft(meta, body, path)
        _log(f"media: added {len(uploads)} file(s) to {draft_id}")
        return _draft_detail(draft_id)

    @app.delete("/api/drafts/<draft_id>/media/<int:index>")
    @guarded
    def api_draft_media_remove(draft_id, index):
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return {"error": "No draft with that id."}, 404
        meta, body = drafts.read_draft(path)
        queue.assert_editable(meta)
        media_list = drafts.get_media(meta)
        if index < 0 or index >= len(media_list):
            return {"error": "No item at that position."}, 404
        removed = media_list.pop(index)
        try:  # best-effort file cleanup; a missing file is not an error
            (config.BASE_DIR / removed["path"]).unlink()
        except OSError:
            pass
        drafts.set_media(meta, media_list)
        drafts.write_draft(meta, body, path)
        return _draft_detail(draft_id)

    @app.post("/api/drafts/<draft_id>/media/<int:index>/alt")
    @guarded
    def api_draft_media_alt(draft_id, index):
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return {"error": "No draft with that id."}, 404
        meta, body = drafts.read_draft(path)
        queue.assert_editable(meta)
        media_list = drafts.get_media(meta)
        if index < 0 or index >= len(media_list):
            return {"error": "No photo at that position."}, 404
        data = request.get_json(silent=True) or {}
        media_list[index]["alt"] = (data.get("alt") or "").strip()
        drafts.set_media(meta, media_list)
        drafts.write_draft(meta, body, path)
        return _draft_detail(draft_id)

    @app.post("/api/drafts/<draft_id>/media/<int:index>/describe")
    @guarded
    def api_draft_media_describe(draft_id, index):
        """Generate alt text for one photo with Claude's vision and save it."""
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return {"error": "No draft with that id."}, 404
        meta, body = drafts.read_draft(path)
        queue.assert_editable(meta)
        media_list = drafts.get_media(meta)
        if index < 0 or index >= len(media_list):
            return {"error": "No photo at that position."}, 404
        abspath = (config.BASE_DIR / media_list[index]["path"]).resolve()
        base = config.BASE_DIR.resolve()
        if base not in abspath.parents or not abspath.exists():
            return {"error": "Media file missing."}, 404
        media_type = IMAGE_MEDIA_TYPES.get(abspath.suffix.lower())
        if not media_type:
            return {"error": "Alt text descriptions are for photos, not videos."}, 400
        _log(f"describe: generating alt text for a photo on {draft_id} with Claude vision")
        alt = llm.describe_image(abspath.read_bytes(), media_type)
        media_list[index]["alt"] = alt
        drafts.set_media(meta, media_list)
        drafts.write_draft(meta, body, path)
        return {"index": index, "alt": alt}

    @app.get("/api/drafts/<draft_id>/media/<int:index>")
    def api_draft_media_get(draft_id, index):
        path = drafts.draft_path(draft_id)
        if not path.exists():
            return {"error": "No draft with that id."}, 404
        meta, _body = drafts.read_draft(path)
        media_list = drafts.get_media(meta)
        if index < 0 or index >= len(media_list):
            return {"error": "No item at that position."}, 404
        abspath = (config.BASE_DIR / media_list[index]["path"]).resolve()
        base = config.BASE_DIR.resolve()
        # Only ever serve files under the project root, never an arbitrary path from meta.
        if base not in abspath.parents or not abspath.exists():
            return {"error": "Media file missing."}, 404
        return send_file(abspath)

    def _publish_one(draft_id: str, path):
        """Shared publish for a single draft file. Returns (ok, error_or_None)."""
        meta, body = drafts.read_draft(path)
        if len(body.split()) > config.MAX_POST_WORDS:
            return False, f"Over the {config.MAX_POST_WORDS}-word limit; trim before publishing."

        def write_draft(updated_meta, updated_body):
            drafts.write_draft(updated_meta, updated_body, path)

        _log(f"publish: posting {draft_id} to LinkedIn")
        ok = queue.publish_draft(meta, body, write_draft)
        if ok:
            _log(f"publish: {draft_id} posted")
            return True, None
        after, _ = drafts.read_draft(path)
        error = after.get("publish_error", "Publish failed.")
        _log(f"publish: {draft_id} failed ({error})")
        return False, error

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
        words = len(body.split())
        if words > config.MAX_POST_WORDS:
            return {"error": f"Draft is {words} words, over the {config.MAX_POST_WORDS}-word limit. Trim it before publishing."}, 400
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
    _log("server: LinkedIn Blogger started, ready")
    url = f"http://{host}:{port}"
    print(f"LinkedIn Blogger running at {url}")
    print("Local only: nothing is exposed to the internet. Stop with Ctrl+C.")
    if open_browser:
        webbrowser.open(url)
    app.run(host=host, port=port, debug=False)
