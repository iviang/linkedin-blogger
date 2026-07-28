"""Weekly email nudge: remind the owner to run the posting workflow.

The nudge drafts nothing and publishes nothing. Optional --prepare runs ingest first
so reference.md is fresh when the owner sits down to write.
"""

import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from . import agent_log, config, state


def _require_smtp():
    config.require("SMTP_HOST", config.SMTP_HOST)
    config.require("SMTP_USER", config.SMTP_USER)
    config.require("SMTP_PASSWORD", config.SMTP_PASSWORD)
    config.require("NUDGE_FROM", config.NUDGE_FROM)
    config.require("NUDGE_TO", config.NUDGE_TO)


def build_message(prepared: bool) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "Time to draft your LinkedIn post"
    msg["From"] = config.NUDGE_FROM
    msg["To"] = config.NUDGE_TO

    intro = "Your weekly LinkedIn post workflow is ready when you are."
    if prepared:
        intro = (
            "Reference material was refreshed from your logs and GitHub activity. "
            "Your weekly LinkedIn post workflow is ready when you are."
        )

    body = f"""{intro}

Suggested steps:
  1. python blogger.py brainstorm
  2. python blogger.py select <number>
  3. python blogger.py skeleton
  4. Fill the gaps, then python blogger.py check <id>
  5. python blogger.py approve <id> --at <when-you-want-it-live>
  6. Scheduled posts publish automatically when due (run publish on a timer)

Nothing publishes without your approval.
"""
    msg.set_content(body)
    return msg


def _anchor() -> datetime | None:
    """Reference point for 'due': the later of your last post and last nudge."""
    times = [t for t in (state.get_last_posted_at(), state.get_last_nudged_at()) if t]
    return max(times) if times else None


def next_due() -> datetime | None:
    anchor = _anchor()
    if anchor is None:
        return None  # nothing posted or nudged yet: due now
    return anchor + timedelta(days=config.NUDGE_INTERVAL_DAYS)


def is_due(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    due = next_due()
    return due is None or now >= due


def send_nudge(prepare: bool = False, force: bool = False) -> None:
    """Email the nudge if it is due (or forced). Optionally refresh reference.md first.

    Due means at least NUDGE_INTERVAL_DAYS have passed since your last post or last nudge,
    so the reminder tracks your posting cadence rather than a fixed calendar day. Schedule
    this to run daily; it stays quiet until a post is actually due.
    """
    now = datetime.now(timezone.utc)
    if not force and not is_due(now):
        due = next_due()
        local_due = due.astimezone().isoformat(timespec="seconds")
        print(f"Not due yet. Next nudge on or after {local_due}. Use --force to send now.")
        return

    _require_smtp()
    if prepare:
        agent_log.run_ingest()

    msg = build_message(prepared=prepare)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
        server.starttls()
        server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.send_message(msg)

    state.set_last_nudged_at(now)
    print(f"Nudge sent to {config.NUDGE_TO}")
