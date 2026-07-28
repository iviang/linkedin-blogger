# LinkedIn Blogger: design and handoff notes

This is the portable context for the project, so any editor or AI assistant (Cursor,
Claude Code, etc.) can pick up where we left off. It captures the design, the decisions,
and the current build status.

## Purpose

A single-user personal tool that turns a week of work notes into regularly scheduled
LinkedIn posts, built on AI automation with a human-in-the-loop (HCI) design. You stay the
author: automation handles gathering, ideating, and posting; every judgment call is yours.

Scope: single user (the owner), posting to their own personal profile. Turning it into a
multi-tenant web app (e.g. Vercel, users bring their own tokens) is a possible later phase,
but that is a separate project with real security, compliance, and LinkedIn app-review
weight, and it is a roadmap decision for Andrew, not assumed here.

## The pipeline

Full flowchart: `docs/pipeline.svg` (high level), plus a detailed Lucidchart and a spec
page the owner has. Three stages:

- **Ingestion.** Two logs feed one reference file: the owner's Markdown `activity_log.md`,
  and an AI agent log built from GitHub activity synthesized into readable milestones,
  errors, and next steps (not raw commit dumps) plus freeform `agent_notes.md`. The
  reference file holds everything since the datetime of the last live post.
- **Processing.** Claude brainstorms a configurable number of post ideas (default 3). The
  owner selects one and adds comments, or reshuffles with new suggestions. Claude writes a
  skeleton draft with fill-in gaps in a consistently named file; the owner fills the gaps
  in their own voice and optionally attaches media. An automated error check flags
  discrepancies, grammar, formatting, factualness, and length; the owner overrides a flag
  or inputs changes (which reruns the check) until they approve a preview.
- **Deliverable.** Approved posts wait in a queue the owner can still pull and edit, up
  until 15 minutes before the scheduled time, then LinkedIn publishes on schedule. The post
  datetime is saved as the new window start for the next reference file.

## Key decisions

- Post to the owner's **personal profile** (`urn:li:person`, `w_member_social`), not a
  Company Page.
- Activity log is a **Markdown file** for now (Notion was considered, deferred).
- AI agent log = **GitHub facts synthesized into readable narrative** plus assistant notes;
  never a wall of commit hashes.
- Ingestion window is **since the last live post**, not a fixed lookback.
- Cadence is **weekly**, and the automation is a "come write your post" **email nudge**
  (owner is off Telegram): it drafts and notifies, publishing stays human-gated.
- Idea count is **configurable (default 3)**.
- Queue stays **editable until 15 minutes** before the scheduled time, then locks.
- **Official LinkedIn API only**, never scraping or browser automation.
- Secrets live in a **gitignored `.env`**. Drafting model: `claude-sonnet-5`.
- Design principle: **nothing publishes without human approval.**

## Architecture (module map)

```
linkedin-blogger/
  blogger.py                 thin entry point: python blogger.py <cmd>
  linkedin_blogger/          the package
    __init__.py __main__.py  package + python -m linkedin_blogger
    cli.py                   argparse CLI, orchestrates the commands
    config.py                settings from .env; BASE_DIR is the repo root
    auth.py                  LinkedIn OAuth (login, token refresh, member URN)
    linkedin.py              publish to the LinkedIn Posts API
    content.py               drafting (to be reworked into the brainstorm/skeleton flow)
    state.py                 state.json helpers, incl. last_posted_at
    github_activity.py       fetch commits + PRs for configured repos (Stage A)
  docs/                      privacy.html (Pages), pipeline.svg, this file
  activity_log.example.md    template; real activity_log.md is gitignored
```

Data files (`.env`, `tokens.json`, `state.json`, `activity_log.md`, `agent_notes.md`,
`agent_log.md`, `reference.md`, `drafts/`) stay at the repo root and are gitignored.

## Build status

**Working today**

- LinkedIn OAuth login (`login`)
- Draft from a single `activity_log.md` (`draft`), review/list/show/approve, publish text
- GitHub activity fetch and preview (`activity`) — Stage A step 1

**Next up**

- Stage A step 2: Claude synthesizes GitHub activity + `agent_notes.md` into a readable
  `agent_log.md`, then merges it with `activity_log.md` into `reference.md` (since-last-post
  window).
- Stage B (Processing): multi-idea brainstorm with select/reshuffle, skeleton-with-gaps,
  the error-check loop and preview.
- Stage C (Deliverable): publish queue with the 15-minute lock, weekly email nudge, media
  attachment. Make the posted-vs-failed state bulletproof (a draft is only `posted` when
  LinkedIn confirms it).

## Prerequisites and secrets (in `.env`)

- `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` (personal-profile app: Sign In with
  LinkedIn OIDC + Share on LinkedIn; redirect `http://localhost:8000/callback`)
- `ANTHROPIC_API_KEY`
- `GITHUB_TOKEN` (read-only fine-grained PAT: Contents + Pull requests read),
  `GITHUB_REPOS` (comma-separated `owner/repo`)
- `SMTP` / Gmail app password for the weekly nudge (planned)

## Guardrails

- Official API only, no scraping (protects the account).
- Posts report only what is in the logs; no invented achievements.
- Human approval gate is non-negotiable; the queue is editable until the 15-minute lock.
