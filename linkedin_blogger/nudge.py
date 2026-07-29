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

    if prepared:
        intro = (
            "It has been a while since your last post. Your reference material was just "
            "refreshed from your logs and GitHub activity, so you can skip step 1 and start at "
            "brainstorm."
        )
    else:
        intro = "It has been a while since your last post. Time to write the next one."

    body = f"""{intro}

Run these from the linkedin-blogger folder, using your virtualenv's python (ID and N are
placeholders you replace, no angle brackets):

  1. python blogger.py ingest        gather notes and GitHub activity since your last post
  2. python blogger.py brainstorm    get a few post ideas
  3. python blogger.py select N      pick one (add --comment "direction"), or reshuffle
  4. python blogger.py skeleton      draft with fill-in gaps
  5. fill every [YOUR VOICE: ...] gap in the draft file, then:
  6. python blogger.py check ID      check grammar, length, and factual claims
  7. python blogger.py attach ID path/to/image.png    optional photo
  8. python blogger.py approve ID --at 2026-01-01T09:00:00-08:00    queue it for a time

Scheduled posts publish automatically when due. Nothing publishes without your approval.
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
    # Tracks the owner's posting cadence (the N-days setting), so changing it moves the nudge.
    return anchor + timedelta(days=state.get_post_interval_days())


def is_due(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    due = next_due()
    return due is None or now >= due


def send_nudge(prepare: bool = False, force: bool = False) -> None:
    """Email the nudge if it is due (or forced). Optionally refresh reference.md first.

    Due means at least your posting interval (the N-days setting) has passed since your last
    post or last nudge, so the reminder tracks your posting cadence rather than a fixed
    calendar day. Schedule this to run daily; it stays quiet until a post is actually due.
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
