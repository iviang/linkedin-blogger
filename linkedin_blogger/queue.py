"""Publish queue: schedule posts, enforce the 15-minute edit lock, publish when due.

Approved drafts stay editable until QUEUE_LOCK_MINUTES before scheduled_at. A draft is
only marked posted after LinkedIn returns a post URN.
"""

from datetime import datetime, timedelta, timezone

from . import config, drafts, linkedin, media, state

# Statuses the owner can still edit (when not within the lock window).
EDITABLE = {"pending", "skeleton", "approved", "queued", "failed"}


def _aware(when: datetime) -> datetime:
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def parse_scheduled_at(value: str) -> datetime:
    """Parse an ISO datetime from CLI input or draft front matter."""
    try:
        return _aware(datetime.fromisoformat(value))
    except ValueError as exc:
        raise SystemExit(
            f"Invalid datetime {value!r}. Use ISO format, e.g. 2026-07-28T09:00:00-07:00"
        ) from exc


def lock_deadline(scheduled_at: datetime) -> datetime:
    return _aware(scheduled_at) - timedelta(minutes=config.QUEUE_LOCK_MINUTES)


def is_locked(scheduled_at: datetime | None) -> bool:
    if scheduled_at is None:
        return False
    return datetime.now(timezone.utc) >= lock_deadline(_aware(scheduled_at))


def is_due(scheduled_at: datetime | None) -> bool:
    if scheduled_at is None:
        return True
    return datetime.now(timezone.utc) >= _aware(scheduled_at)


def assert_editable(meta: dict) -> None:
    status = meta.get("status", "")
    if status in ("posted", "posting"):
        raise SystemExit(f"Draft {meta.get('id')} is {status} and cannot be edited.")
    if status not in EDITABLE:
        raise SystemExit(f"Draft {meta.get('id')} has status {status!r} and cannot be edited.")

    scheduled_raw = meta.get("scheduled_at")
    if scheduled_raw and is_locked(parse_scheduled_at(scheduled_raw)):
        deadline = lock_deadline(parse_scheduled_at(scheduled_raw))
        raise SystemExit(
            f"Draft {meta.get('id')} is locked for publishing until "
            f"{deadline.isoformat(timespec='seconds')} "
            f"({config.QUEUE_LOCK_MINUTES} minutes before the scheduled time)."
        )


def queue_meta(scheduled_at: datetime) -> dict:
    return {
        "status": "queued",
        "scheduled_at": _aware(scheduled_at).isoformat(timespec="seconds"),
    }


def ready_to_publish(meta: dict) -> bool:
    status = meta.get("status")
    if status not in ("approved", "queued"):
        return False
    scheduled_raw = meta.get("scheduled_at")
    scheduled = parse_scheduled_at(scheduled_raw) if scheduled_raw else None
    return is_due(scheduled)


def resolve_media(meta: dict) -> list[dict]:
    """Turn a draft's stored photo list into {path, alt} entries with absolute, existing
    paths, ready for the LinkedIn upload. Raises if a referenced file is missing."""
    resolved = []
    for item in drafts.get_media(meta):
        path = config.BASE_DIR / item["path"]
        if not path.exists():
            raise SystemExit(f"Media file not found: {path}")
        resolved.append({"path": path, "alt": item.get("alt", ""), "kind": media.kind(path)})
    return resolved


def publish_draft(meta: dict, body: str, write_draft) -> bool:
    """Publish one draft. Returns True on success. Never marks posted without a URN."""
    draft_id = meta.get("id", "?")
    meta = dict(meta)

    def _fail(message: str) -> bool:
        meta["status"] = "failed"
        meta["publish_error"] = message
        meta["failed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        write_draft(meta, body)
        print(f"  failed: {message}")
        return False

    # Validate before writing 'posting': a word-limit breach or a missing media file should
    # leave the draft 'failed' (editable, retryable), never stranded mid-publish.
    words = len(body.split())
    if words > config.MAX_POST_WORDS:
        return _fail(f"Over the {config.MAX_POST_WORDS}-word limit ({words} words); trim before publishing.")
    try:
        items = resolve_media(meta)
    except SystemExit as exc:
        return _fail(str(exc))

    meta["status"] = "posting"
    write_draft(meta, body)

    try:
        urn = linkedin.publish_post(body, media=items)
    except linkedin.PublishError as exc:
        meta["status"] = "failed"
        meta["publish_error"] = str(exc)
        meta["failed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        write_draft(meta, body)
        print(f"  failed: {exc}")
        return False
    except Exception as exc:
        # A network drop or timeout must not leave the draft stuck in "posting" (no
        # recovery path). Record it as failed so it is recoverable, but a timeout can
        # happen *after* LinkedIn accepted the post, so the outcome is genuinely unknown:
        # warn the owner to verify on LinkedIn before retrying, to avoid a double post.
        # SystemExit/KeyboardInterrupt derive from BaseException and are not caught here.
        meta["status"] = "failed"
        meta["publish_error"] = f"unknown outcome (verify on LinkedIn before retry): {exc}"
        meta["failed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        write_draft(meta, body)
        print(f"  failed with unknown outcome, check LinkedIn before retrying: {exc}")
        return False

    posted_at = datetime.now(timezone.utc)
    meta["status"] = "posted"
    meta["posted_urn"] = urn
    meta["posted_at"] = posted_at.isoformat(timespec="seconds")
    meta.pop("publish_error", None)
    meta.pop("failed_at", None)
    write_draft(meta, body)
    state.set_last_posted_at(posted_at)
    print(f"  posted as {urn}")
    return True


def format_queue_line(meta: dict, preview: str) -> str:
    status = meta.get("status", "?")
    draft_id = meta.get("id", "?")
    scheduled_raw = meta.get("scheduled_at")
    lock_note = ""
    if scheduled_raw:
        scheduled = parse_scheduled_at(scheduled_raw)
        lock_note = f"  scheduled {scheduled.isoformat(timespec='seconds')}"
        if is_locked(scheduled):
            lock_note += "  [LOCKED]"
        elif status in ("queued", "approved"):
            lock_note += f"  editable until {lock_deadline(scheduled).isoformat(timespec='seconds')}"
    return f"[{status:8}] {draft_id}{lock_note}  {preview}..."
