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
- **One cadence setting.** `post_interval_days` (stored in `state.json`, default from
  `POST_INTERVAL_DAYS`) drives both the default spacing between scheduled posts and the nudge
  cadence, so the owner has a single "post every N days" control.
- **Word limit.** A draft over `MAX_POST_WORDS` (default 220) cannot be queued or published
  from either front-end until trimmed.

## Web interface (local Flask app)

`blogger.py serve` starts a Flask app bound to `127.0.0.1` (localhost only; nothing is
exposed to the network), the main way the tool is used. The browser drives the same pipeline
the CLI implements, over a JSON API in `web.py`; `webui/index.html` is a single
self-contained page (no build step, no external assets). Drafts and state are the same files
the CLI reads, so the two front-ends are interchangeable.

Layout: a left sidebar (pipeline stepper, status chips, reference vintage and next-nudge
countdown, the queue, settings), a main column (a collapsible "start a post" compose card,
the always-open editor, and the drafts list), and a right work sidebar (error check, version
history, queue for publishing, preview, and a collapsible terminal).

Notable behaviors:

- **Freestyle editor.** Always open. Typing creates a draft on first save
  (`POST /api/drafts/new`); a new skeleton auto-opens into it. Debounced autosave; the body
  grows to fit its content so a highlighted excerpt never hides in overflow.
- **Media.** Several photos and one video per draft, stored as a JSON list on the draft's
  front matter (`drafts.get_media`/`set_media`) under `drafts/uploads`. Each photo gets auto
  alt text from Claude vision (`llm.describe_image`). `media.py` validates each file against
  LinkedIn's documented size and length limits (video duration read from the MP4 `mvhd`
  atom). A single post holds one image, several images, or one video, never a mix.
- **Error check.** The same reference check as the CLI. Flags are color-coded by category and
  clickable to select their excerpt in the editor; a suggested fix can be accepted to replace
  the excerpt in place (`processing.apply_suggestion`). The over-length flag is computed
  locally, shown the moment a draft opens, and cannot be overridden.
- **Version history.** `versions.py` snapshots on create, edit (rapid edits coalesced),
  check, override, accept, and restore, under `drafts/versions`. The UI lists them and offers
  a word-level-diff compare and a restore.
- **Terminal.** `web.py` keeps a small in-memory activity feed (per server run) at
  `/api/activity`; the collapsible panel polls it and shows pipeline steps (Claude and
  LinkedIn calls, publishes, and so on).
- **Publish safety.** `queue.publish_draft` resolves media and checks the word limit before
  writing `posting`, so a missing file or an over-length post leaves the draft `failed`
  (editable, retryable) rather than stranded, and it never writes `posted` without a
  confirmed URN.

## Design principles (HCI)

The design was reached by instinct (keep my own voice, lower the anxiety of a blank post),
and it maps cleanly onto established human-computer interaction work. This section names the
principles so the "built on HCI principles" claim is defensible, not decorative. The honest
framing: the design *embodies* these, it was not formally derived from the papers.

Anchors (the two strongest, unarguable):

- **Mixed-initiative interaction** (Horvitz, 1999). The system proposes (ideas, a skeleton),
  the human disposes (selects, edits, approves). This is the shape of the whole pipeline.
- **Levels of automation** (Sheridan and Verplank; Parasuraman, Sheridan and Wickens). A
  deliberate choice to sit at "the computer suggests and executes only after human approval,"
  with the gate in a known place, rather than defaulting to full autonomy.

Supporting principles mapped to decisions:

- **Guards against automation bias / complacency.** Never auto-posts; the human is the gate,
  so there is no rubber-stamping an AI post.
- **Nielsen usability heuristics:** user control and freedom (`override`, `reshuffle`, pull
  from the queue to edit); visibility of system status (`preview`, `queue`, lock state,
  check results); error prevention and recovery (the error check names issues with excerpts
  and lets you fix or override; the 15-minute lock prevents last-second edits going live).
- **Scaffolding and cognitive load.** The skeleton-with-gaps structures a hard task (the
  blank page) and is removed once filled, which is the direct answer to the anxiety problem.
- **Progressive disclosure / chunking.** "Write a post" is split into one-decision-per-step
  stages so complexity stays manageable.
- **Behavioral design (adjacent, not core HCI).** The cadence-based email nudge is a trigger
  (Fogg Behavior Model) that lands when effort is low because the skeleton lowered it. Real,
  but do not lead with it as HCI proof.

If asked "which HCI principles," lead with mixed-initiative interaction and levels of
automation.

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
    processing.py            Stage B: brainstorm, skeleton, error-check loop, accept-fix
    queue.py                 Stage C: scheduled queue, lock, reliable publish
    nudge.py                 Stage C: email reminder (cadence = the post-interval setting)
    state.py                 state.json helpers: last_posted_at, settings, brainstorm session
    github_activity.py       fetch commits + PRs for configured repos (Stage A step 1)
    agent_log.py             synthesize agent_log.md + merge reference.md (Stage A step 2)
    llm.py                   shared Anthropic client (retries, thinking off, image alt text)
    drafts.py                read/write draft files, media-list helpers, soft delete to trash
    media.py                 media kind detection and LinkedIn size/length validation
    versions.py              per-draft version snapshots for compare and restore
    web.py                   local Flask app and JSON API behind the browser UI
    webui/index.html         the single self-contained browser page (no build step)
  docs/                      privacy.html (Pages), pipeline.svg, this file
  activity_log.example.md    template; real activity_log.md is gitignored
```

Data files (`.env`, `tokens.json`, `state.json`, `activity_log.md`, `agent_notes.md`,
`agent_log.md`, `reference.md`, `drafts/`) stay at the repo root and are gitignored. Under
`drafts/`: the draft `.md` files and their `.check.json`, plus `uploads/` (attached media),
`versions/` (snapshots), and `trash/` (soft-deleted drafts). Nothing secret is ever written
to a tracked path.

## Build status

**Verified at runtime**

- LinkedIn OAuth `login` and profile fetch (name and photo).
- The web app end to end in a browser: ingest, brainstorm and reshuffle, skeleton, edit with
  autosave, error check with accept-fix and override, version history compare and restore,
  multi-photo upload with auto alt text, scheduling, the word-limit block, and the terminal
  activity feed. The equivalent CLI commands load and run (`list`, `activity`, and the rest).

**Not yet exercised against the live service**

- The real publish to LinkedIn (it posts to your live feed) and the video upload path, and
  the SMTP nudge email. These are built and unit-checked; watch the first real publish and
  nudge, and a returned error message will name what to fix.

**Next up**

- Wire the schedule (Task Scheduler): run `nudge --prepare` and `publish` periodically. The
  nudge self-gates on the post-interval setting (default 7 days) since your last post or
  nudge, so a periodic run only emails when a post is actually due; `publish` only posts
  drafts whose scheduled time has arrived.

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
- A draft over the word limit cannot be queued or published; publish fails safely (leaving
  the draft `failed`, never stranded) on a missing media file or an over-length post.
- Uploaded media and version snapshots live under gitignored `drafts/`; no secret is written
  to a tracked path. The web server binds to localhost only.
