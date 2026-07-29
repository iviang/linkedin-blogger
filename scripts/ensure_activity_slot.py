"""Ensure activity_log.md has a dated heading for today, so there is always a fresh slot to
fill in as you work. Idempotent: adds today's `## YYYY-MM-DD` section only when it is missing.

Called by the git pre-commit hook (on every commit) and, optionally, by a Claude Code
UserPromptSubmit hook (on every prompt), so a day you make changes always gets a slot. Prints
only to stderr, so nothing is injected into a Claude prompt's context.
"""

import sys
from datetime import date
from pathlib import Path

LOG = Path(__file__).resolve().parent.parent / "activity_log.md"


def main() -> None:
    if not LOG.exists():
        return
    today = date.today().isoformat()
    text = LOG.read_text(encoding="utf-8")
    headings = {line.strip() for line in text.splitlines()}
    if f"## {today}" in headings:
        return
    separator = "" if text.endswith("\n") else "\n"
    LOG.write_text(text + separator + f"\n## {today}\n", encoding="utf-8")
    print(f"activity_log.md: added a slot for {today}", file=sys.stderr)


if __name__ == "__main__":
    main()
