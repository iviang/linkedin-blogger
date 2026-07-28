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
    content.py               legacy one-shot draft from activity_log.md
    processing.py            Stage B: brainstorm, skeleton, error-check loop
    queue.py                 Stage C: scheduled queue, lock, reliable publish
    nudge.py                 Stage C: weekly email reminder
    state.py                 state.json helpers, incl. last_posted_at
    github_activity.py       fetch commits + PRs for configured repos (Stage A step 1)
    agent_log.py             synthesize agent_log.md + merge reference.md (Stage A step 2)
  docs/                      privacy.html (Pages), pipeline.svg, this file
  activity_log.example.md    template; real activity_log.md is gitignored
```

Data files (`.env`, `tokens.json`, `state.json`, `activity_log.md`, `agent_notes.md`,
`agent_log.md`, `reference.md`, `drafts/`) stay at the repo root and are gitignored.

## Build status

**Verified at runtime**

- LinkedIn OAuth `login`, `list`, GitHub activity preview (`activity`), and the legacy
  one-shot `draft` / `approve` / `publish` text flow.

**Built and compiling, not yet end-to-end tested**

- Stage A step 2: agent log synthesis + reference merge (`ingest`).
- Stage B: `brainstorm`, `ideas`, `select`, `reshuffle`, `skeleton`, `check`, `override`,
  `preview` (multi-idea, skeleton-with-gaps, error-check loop).
- Stage C: `approve` / `schedule` / `queue` (15-minute lock), `attach` media, `publish`
  (marks posted only on a confirmed URN, else failed, with unknown-outcome recorded safely),
  `retry`, `nudge` email.

**Next up**

- End-to-end runtime test of the full flow: ingest -> brainstorm -> select -> skeleton ->
  check -> approve -> publish, on a machine with dependencies installed.
- Wire the schedule (Task Scheduler): run `nudge --prepare` and `publish` daily. The nudge
  self-gates on NUDGE_INTERVAL_DAYS since your last post (default 7), so a daily run only
  emails when a post is actually due; `publish` only posts drafts whose scheduled time is
  due.
- Optional: video media.

## Prerequisites and secrets (in `.env`)

- `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` (personal-profile app: Sign In with
  LinkedIn OIDC + Share on LinkedIn; redirect `http://localhost:8000/callback`)
- `ANTHROPIC_API_KEY`
- `GITHUB_TOKEN` (read-only fine-grained PAT: Contents + Pull requests read),
  `GITHUB_REPOS` (comma-separated `owner/repo`)
- `SMTP` / Gmail app password for the weekly nudge

## Guardrails

- Official API only, no scraping (protects the account).
- Posts report only what is in the logs; no invented achievements.
- Human approval gate is non-negotiable; the queue is editable until the 15-minute lock.
