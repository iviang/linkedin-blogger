"""Fetch recent GitHub activity (commits and pull requests) for the configured repos.

Read-only. This returns raw facts only; turning them into a readable narrative happens in
agent_log.py so the fetch stays simple and testable on its own.
"""

from datetime import datetime, timezone

import requests

from . import config

API = "https://api.github.com"


def _headers() -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    return headers


def _as_utc(when: datetime | None) -> datetime | None:
    if when is None:
        return None
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)


def _parse_gh_time(value: str) -> datetime:
    # GitHub returns e.g. "2026-07-27T21:20:47Z"; make it aware for safe comparison.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _get(url: str, params: dict) -> requests.Response:
    """GET with friendly errors for the auth/access failures we expect."""
    resp = requests.get(url, headers=_headers(), params=params, timeout=30)
    if resp.status_code == 401:
        raise SystemExit(
            "GitHub rejected the token (401). Check GITHUB_TOKEN in .env: it must be a valid, "
            "unexpired PAT, not the placeholder from .env.example."
        )
    if resp.status_code == 403:
        raise SystemExit("GitHub returned 403: rate-limited, or the token lacks the needed read access.")
    if resp.status_code == 404:
        raise SystemExit(f"Repo not found, or the token cannot see it: {url}. Check GITHUB_REPOS and token scope.")
    return resp


def _commits(repo: str, since: datetime | None) -> list[dict]:
    params = {"per_page": 100}
    if since:
        params["since"] = since.isoformat()
    resp = _get(f"{API}/repos/{repo}/commits", params)
    if resp.status_code == 409:  # empty repository has no commits
        return []
    resp.raise_for_status()
    return [
        {
            "repo": repo,
            "date": c["commit"]["author"]["date"],
            "message": c["commit"]["message"].splitlines()[0],
        }
        for c in resp.json()
    ]


def _pulls(repo: str, since: datetime | None) -> list[dict]:
    # Pulls have no "since" filter, so sort by most-recently-updated and stop early.
    params = {"state": "all", "sort": "updated", "direction": "desc", "per_page": 50}
    resp = _get(f"{API}/repos/{repo}/pulls", params)
    resp.raise_for_status()
    out = []
    for pull in resp.json():
        updated = _parse_gh_time(pull["updated_at"])
        if since and updated < since:
            break
        out.append(
            {
                "repo": repo,
                "number": pull["number"],
                "title": pull["title"],
                "state": "merged" if pull.get("merged_at") else pull["state"],
                "updated": pull["updated_at"],
            }
        )
    return out


def fetch_activity(since: datetime | None) -> dict:
    """Return {'commits': [...], 'pulls': [...]} across all configured repos since `since`.

    Raises SystemExit with a clear message if no repos are configured.
    """
    if not config.GITHUB_REPOS:
        raise SystemExit("No repos configured. Set GITHUB_REPOS in .env (owner/repo, comma-separated).")

    since = _as_utc(since)
    commits: list[dict] = []
    pulls: list[dict] = []
    for repo in config.GITHUB_REPOS:
        commits += _commits(repo, since)
        pulls += _pulls(repo, since)
    return {"commits": commits, "pulls": pulls}
