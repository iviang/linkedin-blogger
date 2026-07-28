"""Central configuration. All secrets come from the environment, never the source tree."""

import os
from pathlib import Path

from dotenv import load_dotenv

# BASE_DIR is the repo root (the parent of this package), where .env and the per-user
# data files live. Data stays at the project root, not inside the package.
BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

# Files that hold per-user state. Kept out of git via .gitignore because they
# contain tokens (tokens.json) or personal scheduling state (state.json).
TOKENS_FILE = BASE_DIR / "tokens.json"
STATE_FILE = BASE_DIR / "state.json"
ACTIVITY_LOG = BASE_DIR / "activity_log.md"
DRAFTS_DIR = BASE_DIR / "drafts"

LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
LINKEDIN_REDIRECT_URI = os.getenv("LINKEDIN_REDIRECT_URI", "http://localhost:8000/callback")
# LinkedIn keeps each monthly API version active for about a year, so this
# default goes stale. If the API returns 426 NONEXISTENT_VERSION, bump it (or
# LINKEDIN_API_VERSION in .env) to a recent YYYYMM from the LinkedIn changelog.
LINKEDIN_API_VERSION = os.getenv("LINKEDIN_API_VERSION", "202607")

# Scopes: openid + profile let us read the member id via /v2/userinfo;
# w_member_social is the permission that actually allows posting as the member.
LINKEDIN_SCOPES = "openid profile w_member_social"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

# How far back to look for activity when drafting a new post (legacy draft command).
DRAFT_LOOKBACK_DAYS = int(os.getenv("DRAFT_LOOKBACK_DAYS", "7"))

# Stage B: how many ideas Claude brainstorms before the owner picks one.
BRAINSTORM_IDEA_COUNT = int(os.getenv("BRAINSTORM_IDEA_COUNT", "3"))

# --- Ingestion: GitHub agent log (Stage A) ---
# Read-only PAT (fine-grained, Contents: Read is enough) so private repos are visible.
# Public repos work without a token but hit tighter unauthenticated rate limits.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Comma-separated "owner/repo" entries the agent log tracks, e.g.
# GITHUB_REPOS=iviang/linkedin-blogger,iviang/Strscout
GITHUB_REPOS = [r.strip() for r in os.getenv("GITHUB_REPOS", "").split(",") if r.strip()]

# Ingestion working files, all gitignored, kept at the repo root.
AGENT_NOTES = BASE_DIR / "agent_notes.md"     # freeform milestones/errors/next steps you add
AGENT_LOG = BASE_DIR / "agent_log.md"         # generated: readable synthesis of GitHub + notes
REFERENCE_FILE = BASE_DIR / "reference.md"    # generated: merged activity + agent log since last post

# Stage C: publish queue lock window (minutes before scheduled_at when edits stop).
QUEUE_LOCK_MINUTES = int(os.getenv("QUEUE_LOCK_MINUTES", "15"))

# Stage C: weekly email nudge over SMTP (Gmail app password works).
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
NUDGE_FROM = os.getenv("NUDGE_FROM")
NUDGE_TO = os.getenv("NUDGE_TO")


def require(name: str, value):
    """Fail loudly and early when a required setting is missing, rather than
    surfacing a confusing API error later."""
    if not value:
        raise SystemExit(f"Missing required setting: {name}. Add it to your .env file.")
    return value
