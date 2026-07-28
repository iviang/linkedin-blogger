"""Stage A step 2: synthesize GitHub activity into agent_log.md and merge into reference.md.

GitHub fetch stays in github_activity.py; this module turns raw facts plus freeform
agent_notes.md into readable milestones, errors, and next steps, then combines that
with the owner's activity log since the last live post.
"""

import re
from datetime import datetime, timezone

from anthropic import Anthropic

from . import config, github_activity, state

_DATE_HEADING = re.compile(r"^##\s*(\d{4}-\d{2}-\d{2})\b", re.MULTILINE)

_SYNTHESIS_SYSTEM = """You turn raw GitHub activity and optional owner notes into a readable \
agent log for LinkedIn post drafting.

Rules:
- Synthesize commits and pull requests into milestones, errors encountered, and next steps. \
Never dump a wall of commit hashes or one line per commit unless each line is a distinct \
milestone.
- Use only facts from the GitHub data and owner notes. Do not invent work, metrics, or \
outcomes.
- Write in first person for the owner to review.
- Use Markdown with ## headings where helpful (for example Milestones, Errors, Next steps). \
Omit empty sections.
- No em dashes. Use commas, colons, or parentheses instead.
- If there is little or no activity, say so briefly rather than padding.
- Return only the agent log markdown. No preamble, no quotes around it."""


def _read_agent_notes() -> str:
    if not config.AGENT_NOTES.exists():
        return ""
    return config.AGENT_NOTES.read_text(encoding="utf-8").strip()


def _format_github_facts(data: dict) -> str:
    lines: list[str] = []
    if data["commits"]:
        lines.append("Commits:")
        for commit in data["commits"]:
            lines.append(f"- [{commit['repo']}] {commit['date']}: {commit['message']}")
    if data["pulls"]:
        if lines:
            lines.append("")
        lines.append("Pull requests:")
        for pull in data["pulls"]:
            lines.append(
                f"- [{pull['repo']}] #{pull['number']} ({pull['state']}): "
                f"{pull['title']} (updated {pull['updated']})"
            )
    return "\n".join(lines) if lines else "No GitHub activity in this window."


def _fetch_github(since: datetime | None) -> dict:
    if not config.GITHUB_REPOS:
        return {"commits": [], "pulls": []}
    return github_activity.fetch_activity(since)


def activity_since(since: datetime | None) -> str:
    """Return activity_log.md entries on or after `since`'s date.

    Falls back to the whole file when there are no dated headings, matching content.py
    so the tool still works before the date-heading convention is adopted.
    """
    if not config.ACTIVITY_LOG.exists():
        return ""

    text = config.ACTIVITY_LOG.read_text(encoding="utf-8").strip()
    if not text:
        return ""

    matches = list(_DATE_HEADING.finditer(text))
    if not matches:
        return text

    cutoff = since.date() if since else None
    kept: list[str] = []
    for i, match in enumerate(matches):
        entry_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        if cutoff and entry_date < cutoff:
            continue
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        kept.append(text[start:end].strip())

    return "\n\n".join(kept)


def synthesize_agent_log(since: datetime | None) -> str:
    """Ask Claude to write readable agent_log.md content from GitHub facts and notes."""
    config.require("ANTHROPIC_API_KEY", config.ANTHROPIC_API_KEY)

    github_data = _fetch_github(since)
    github_text = _format_github_facts(github_data)
    notes = _read_agent_notes()

    window = since.isoformat(timespec="seconds") if since else "all available history"
    user_parts = [f"Ingestion window: since {window}", "", "GitHub activity:", github_text]
    if notes:
        user_parts.extend(["", "Owner notes (agent_notes.md):", notes])
    user_parts.append("")
    user_parts.append("Write the agent log markdown.")

    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=2048,
        system=_SYNTHESIS_SYSTEM,
        messages=[{"role": "user", "content": "\n".join(user_parts)}],
    )
    # Sonnet 5 uses adaptive thinking, so content[0] can be a ThinkingBlock. Take the
    # first text block instead of assuming position 0.
    return next((b.text for b in message.content if b.type == "text"), "").strip()


def build_reference(since: datetime | None, agent_log_text: str) -> str:
    """Merge the activity-log slice and agent log into reference.md."""
    activity = activity_since(since)
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if since:
        window = f"since {since.astimezone(timezone.utc).isoformat(timespec='seconds')} UTC"
    else:
        window = "beginning (no prior post recorded yet)"

    lines = [
        "# Reference",
        "",
        f"Window: {window}",
        f"Generated: {generated}",
        "",
        "## Activity log",
        "",
        activity if activity else "_No activity log entries in this window._",
        "",
        "## Agent log",
        "",
        agent_log_text,
        "",
    ]
    return "\n".join(lines)


def run_ingest() -> tuple[datetime | None, str, str]:
    """Synthesize agent_log.md and reference.md. Returns (since, agent_log, reference)."""
    since = state.get_last_posted_at()
    agent_log_text = synthesize_agent_log(since)
    config.AGENT_LOG.write_text(agent_log_text + "\n", encoding="utf-8")

    reference_text = build_reference(since, agent_log_text)
    config.REFERENCE_FILE.write_text(reference_text, encoding="utf-8")

    return since, agent_log_text, reference_text
