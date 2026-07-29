"""Per-draft version snapshots for compare and restore.

Snapshots live as JSON under drafts/versions/, so they are gitignored with the rest of
drafts/. A burst of rapid edits collapses into one entry; discrete events (skeleton, check,
override, restore) always get their own. The list is capped so it cannot grow without bound.
"""

import json
from datetime import datetime

from . import config

VERSIONS_DIR = config.DRAFTS_DIR / "versions"
MAX_VERSIONS = 40
COALESCE_SECONDS = 25


def _path(draft_id: str):
    return VERSIONS_DIR / f"{draft_id}.json"


def load(draft_id: str) -> list[dict]:
    path = _path(draft_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save(draft_id: str, versions: list[dict]) -> None:
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    _path(draft_id).write_text(json.dumps(versions, indent=2), encoding="utf-8")


def record(draft_id: str, kind: str, label: str, title: str, body: str, who: str = "you") -> None:
    """Append a snapshot. `kind` is created/edit/check/override/restore; `who` is you/auto."""
    versions = load(draft_id)
    now = datetime.now()
    entry = {
        "kind": kind,
        "label": label,
        "title": title or "",
        "body": body or "",
        "who": who,
        "at": now.isoformat(timespec="seconds"),
    }
    if versions and kind == "edit":
        last = versions[-1]
        # An edit that changed nothing is not a new version.
        if last["title"] == entry["title"] and last["body"] == entry["body"]:
            return
        # Collapse a burst of consecutive edits into the latest one.
        if last.get("kind") == "edit":
            try:
                recent = (now - datetime.fromisoformat(last["at"])).total_seconds() < COALESCE_SECONDS
            except ValueError:
                recent = False
            if recent:
                versions[-1] = entry
                _save(draft_id, versions)
                return
    versions.append(entry)
    if len(versions) > MAX_VERSIONS:
        versions = versions[-MAX_VERSIONS:]
    _save(draft_id, versions)


def get(draft_id: str, index: int) -> dict | None:
    versions = load(draft_id)
    if 0 <= index < len(versions):
        return versions[index]
    return None


def summaries(draft_id: str) -> list[dict]:
    """Lightweight list for the UI: index, label, who, at, and a short preview."""
    out = []
    for i, version in enumerate(load(draft_id)):
        preview = (version.get("body") or "").replace("\n", " ")[:80]
        out.append(
            {
                "index": i,
                "label": version["label"],
                "who": version["who"],
                "at": version["at"],
                "title": version.get("title", ""),
                "preview": preview,
            }
        )
    return out
