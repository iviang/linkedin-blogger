"""Weekly email nudge: remind the owner to run the posting workflow.

The nudge drafts nothing and publishes nothing. Optional --prepare runs ingest first
so reference.md is fresh when the owner sits down to write.
"""

import smtplib
from email.message import EmailMessage

from . import agent_log, config


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


def send_nudge(prepare: bool = False) -> None:
    """Send the weekly nudge email. Optionally refresh reference.md first."""
    _require_smtp()

    if prepare:
        agent_log.run_ingest()

    msg = build_message(prepared=prepare)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
        server.starttls()
        server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.send_message(msg)

    print(f"Nudge sent to {config.NUDGE_TO}")
