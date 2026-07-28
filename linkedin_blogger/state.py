"""Per-user state in state.json (shared with auth.py, which stores the member URN).

The key value here is `last_posted_at`: the timestamp of the most recent live post, which
is the start of the window ingestion collects from. Stored as a timezone-aware UTC ISO
string so it compares cleanly against GitHub's UTC timestamps.
"""

import json
from datetime import datetime, timezone

from . import config


def load() -> dict:
    if config.STATE_FILE.exists():
        return json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save(state: dict) -> None:
    # Read-modify-write preserves keys other code owns (e.g. auth's member_urn).
    config.STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def get_last_posted_at() -> datetime | None:
    raw = load().get("last_posted_at")
    return datetime.fromisoformat(raw) if raw else None


def set_last_posted_at(when: datetime) -> None:
    # Normalize to aware UTC so downstream comparisons never mix naive and aware.
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    state = load()
    state["last_posted_at"] = when.astimezone(timezone.utc).isoformat(timespec="seconds")
    save(state)


def get_last_nudged_at() -> datetime | None:
    raw = load().get("last_nudged_at")
    return datetime.fromisoformat(raw) if raw else None


def set_last_nudged_at(when: datetime) -> None:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    state = load()
    state["last_nudged_at"] = when.astimezone(timezone.utc).isoformat(timespec="seconds")
    save(state)


def get_processing() -> dict:
    return load().get("processing", {})


def save_processing(processing: dict) -> None:
    state = load()
    state["processing"] = processing
    save(state)


def get_post_interval_days() -> int:
    """Days between scheduled posts. The owner's saved setting wins over the config default."""
    return int(load().get("settings", {}).get("post_interval_days", config.POST_INTERVAL_DAYS))


def set_post_interval_days(days: int) -> None:
    state = load()
    settings = state.get("settings", {})
    settings["post_interval_days"] = int(days)
    state["settings"] = settings
    save(state)
