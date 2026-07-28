"""Read draft files (front matter + body). Shared by the web UI so it does not duplicate
the CLI's parsing. Front matter is a block fenced by lines of three dashes; everything
after the closing fence is the body.
"""

from . import config

FENCE = "---"


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
                "media": meta.get("media"),
            }
        )
    return out
