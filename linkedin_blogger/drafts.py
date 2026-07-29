"""Read and write draft files (front matter + body). Shared by the web UI so it does not
duplicate the CLI's parsing. Front matter is a block fenced by lines of three dashes;
everything after the closing fence is the body.
"""

import json
from datetime import datetime

from . import config

FENCE = "---"


def get_media(meta: dict) -> list[dict]:
    """Return a draft's photos as a list of {path, alt}.

    Photos are stored as a JSON array on the `media` front-matter line. Older drafts used a
    single `media` path plus a `media_alt` line, so that shape is read too for compatibility.
    """
    raw = (meta.get("media") or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return [
            {"path": it["path"], "alt": it.get("alt", "")}
            for it in items
            if isinstance(it, dict) and it.get("path")
        ]
    return [{"path": raw, "alt": meta.get("media_alt", "")}]


def set_media(meta: dict, media: list[dict]) -> None:
    """Write the photo list back to meta as JSON, or clear it when empty."""
    meta.pop("media_alt", None)  # legacy single-photo field, folded into the list
    if media:
        meta["media"] = json.dumps([{"path": m["path"], "alt": m.get("alt", "")} for m in media])
    else:
        meta.pop("media", None)


def draft_path(draft_id: str):
    return config.DRAFTS_DIR / f"{draft_id}.md"


def new_draft_id() -> str:
    return datetime.now().strftime("%Y-%m-%d-%H%M%S")


def write_draft(meta: dict, body: str, path) -> None:
    """Write front matter + body. Mirrors the CLI's format so both agree on the file shape."""
    config.DRAFTS_DIR.mkdir(exist_ok=True)
    lines = [FENCE]
    for key, value in meta.items():
        lines.append(f"{key}: {value}")
    lines.append(FENCE)
    lines.append("")
    lines.append(body.strip())
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def delete_draft(draft_id: str) -> bool:
    """Move a draft and its check file into drafts/trash/ instead of unlinking, so a
    mistaken delete is recoverable. Returns False when the draft does not exist.
    """
    path = draft_path(draft_id)
    if not path.exists():
        return False
    trash = config.DRAFTS_DIR / "trash"
    trash.mkdir(parents=True, exist_ok=True)
    path.replace(trash / path.name)
    check = config.DRAFTS_DIR / f"{draft_id}.check.json"
    if check.exists():
        check.replace(trash / check.name)
    return True


def read_draft(path):
    text = path.read_text(encoding="utf-8")
    meta = {}
    body = text
    if text.startswith(FENCE):
        _, block, body = text.split(FENCE, 2)
        for line in block.strip().splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()
    return meta, body.strip()


def list_drafts() -> list[dict]:
    """Return every draft as {id, status, title, preview, scheduled_at, media}, newest last."""
    if not config.DRAFTS_DIR.exists():
        return []
    out = []
    for path in sorted(config.DRAFTS_DIR.glob("*.md")):
        meta, body = read_draft(path)
        out.append(
            {
                "id": meta.get("id", path.stem),
                "status": meta.get("status", "?"),
                "title": meta.get("idea_title", ""),
                "preview": body.replace("\n", " ")[:140],
                "scheduled_at": meta.get("scheduled_at"),
                "media_count": len(get_media(meta)),
                "words": len(body.split()),
            }
        )
    return out
