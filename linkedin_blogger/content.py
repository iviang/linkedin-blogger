"""Legacy one-shot drafting from activity_log.md.

Stage B processing (brainstorm, skeleton, error check) lives in processing.py and reads
reference.md instead. This module remains for the quick `draft` command.
"""

import re
from datetime import datetime, timedelta

from anthropic import Anthropic

from . import config

# Activity entries are expected as "## YYYY-MM-DD" headings followed by notes.
_DATE_HEADING = re.compile(r"^##\s*(\d{4}-\d{2}-\d{2})\b", re.MULTILINE)


def recent_activity(lookback_days: int) -> str:
    """Return the slice of the activity log within the lookback window.

    Falls back to the whole file if no dated headings are found, so the tool still
    works before you have adopted the date-heading convention.
    """
    if not config.ACTIVITY_LOG.exists():
        raise SystemExit(
            f"No activity log at {config.ACTIVITY_LOG}. Create it and add what you worked on."
        )

    text = config.ACTIVITY_LOG.read_text(encoding="utf-8").strip()
    matches = list(_DATE_HEADING.finditer(text))
    if not matches:
        return text

    cutoff = datetime.now().date() - timedelta(days=lookback_days)
    kept = []
    for i, match in enumerate(matches):
        entry_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        if entry_date < cutoff:
            continue
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        kept.append(text[start:end].strip())

    return "\n\n".join(kept)


_SYSTEM_PROMPT = """You write first-person LinkedIn posts for an AI engineering intern \
at Kool Quarters, summarizing their recent work.

Rules:
- Use only facts present in the provided activity notes. Do not invent projects, \
metrics, results, or names. If the notes are thin, write a shorter post.
- First person, warm and professional. No hype, no buzzword salad, no fake humility.
- No em dashes. Use commas, colons, or parentheses instead.
- No hashtag spam. At most two or three relevant hashtags at the end, and only if useful.
- 120 to 220 words. Plain language a hiring manager would respect.
- Do not claim anything is finished or shipped unless the notes say so.
- Return only the post text, ready to publish. No preamble, no quotes around it."""


def draft_post(activity: str) -> str:
    """Ask Claude to draft the post from the activity slice."""
    config.require("ANTHROPIC_API_KEY", config.ANTHROPIC_API_KEY)
    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)

    message = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Here are my activity notes for the period:\n\n{activity}\n\n"
                "Write the LinkedIn post.",
            }
        ],
    )
    # A single text block is expected for this prompt.
    return message.content[0].text.strip()
