# LinkedIn Blogger

Transforming a week of work notes into regularly scheduled LinkedIn posts, built on AI
automation and human-computer interaction (HCI) principles.

A personal tool that drafts LinkedIn posts from your work notes and publishes the ones you
approve, on a weekly schedule. You stay the author: the automation handles the gathering,
ideating, and posting, while every judgment call stays with you. Posts go to your personal
profile through LinkedIn's official API. It runs as a local web app in your browser, or from
the command line.

## The pipeline

Three stages. See [docs/pipeline.svg](docs/pipeline.svg) for the diagram.

- **Ingestion.** Two logs feed one reference file: your own Markdown activity log, and an
  AI agent log that turns raw GitHub activity into readable milestones, errors, and next
  steps. The reference file holds everything since your last live post.
- **Processing.** Claude brainstorms a set number of post ideas (default 3). You select one
  and add comments, or reshuffle for new ideas. Claude builds a skeleton draft with gaps
  you fill in your own voice, optionally attaching media. An automated error check flags
  grammar, formatting, factualness, and length; you override a flag or make changes (which
  reruns the check) until you approve a preview.
- **Deliverable.** Approved posts wait in a queue you can still pull and edit, up until 15
  minutes before the scheduled time, then LinkedIn publishes them. The post time becomes
  the new start point for the next reference file.

The design principle is human-in-the-loop: automation does the gathering, ideating, and
publishing, but which idea, the final wording, and the go-ahead are all yours. Nothing
publishes without your approval.

## Web interface

The main way to use the tool is a local web app. Nothing is hosted: it binds to
`127.0.0.1`, so your notes and tokens never leave the machine.

```bash
python blogger.py serve      # opens http://localhost:5000
```

The whole pipeline runs in the browser:

- **Sidebar.** A pipeline stepper (ingest, brainstorm, write, check, queue), status chips,
  the reference vintage and days until the next nudge, the publish queue, and settings.
- **Start a post.** Run ingest, view the reference, brainstorm ideas (with reshuffle), then
  create a skeleton, which opens straight into the editor. This card collapses while you
  write so the editor takes focus.
- **Editor.** An always-open writing surface: start freestyle (it saves as a draft on the
  first keystroke) or from a skeleton. It autosaves on idle, the body grows to fit its text,
  a word meter tracks the 120 to 220 word target, and spell check is on. Attach several
  photos or a video by drag-and-drop; each photo gets auto-generated alt text (Claude
  vision) that you can edit, and media is checked against LinkedIn's size and length limits.
- **Error check** (side panel). Runs the same reference check, with flags color-coded by
  kind (factualness, grammar, formatting, length). Click a flag to jump to its excerpt in
  the draft. Accept a suggested fix to apply it in place, override a flag you accept, or
  trim an over-length post (that flag cannot be overridden and blocks queuing).
- **Version history** (side panel). Every change is snapshotted. Compare any earlier version
  side by side with the current draft (word-level diff highlighting), and restore it.
- **Queue for publishing** (side panel). Pick a time, or leave it blank to auto-space one
  interval after the queue, then Queue or Publish now. Over-length drafts are blocked from
  both.
- **Preview** opens the post at LinkedIn's width, with your real name and profile photo.
- **Terminal** (collapsible) streams what the pipeline is doing (Claude calls, publishes,
  and so on) as it happens.

Everything the command line does is available here. The CLI still works and is what a
scheduled job (nudge, publish) uses.

## Status

Be honest about what runs today versus what is still only built.

**Verified at runtime**

- LinkedIn OAuth login and profile fetch (name and photo), personal profile,
  `w_member_social` + OpenID Connect.
- The web app end to end in the browser: ingest, brainstorm and reshuffle, skeleton, edit
  with autosave, error check with accept-fix and override, version history compare and
  restore, multi-photo upload with auto alt text, scheduling, the word-limit block, and the
  live activity feed.
- The CLI loads and runs (`list`, `activity`, and the rest).

**Not yet exercised against the live service**

- The actual publish to LinkedIn (it posts to your real feed, so it needs a deliberate live
  post) and the video upload path, and the SMTP nudge email. These code paths are built and
  unit-checked; watch the first real publish and nudge, and if LinkedIn returns an error the
  message will say what to fix.

## Prerequisites

- **Python 3.10+**
- **A LinkedIn developer app** (personal-profile posting): Client ID and Secret, with the
  *Sign In with LinkedIn using OpenID Connect* and *Share on LinkedIn* products, and a
  `http://localhost:8000/callback` redirect URL.
- **An Anthropic API key** with a few dollars of credit.
- **A GitHub personal access token** (read-only: Contents + Pull requests), for the agent log.
- **A Gmail app password**, for the weekly email nudge over SMTP.
- **A `.env` file** holding the above (gitignored), and **Task Scheduler** (or cron) for
  the weekly job.

## Setup

```bash
cd linkedin-blogger
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt   # Windows; sidesteps activation

cp .env.example .env                       # then edit .env with your real keys
cp activity_log.example.md activity_log.md # your working log (gitignored)
```

Fill in `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, and `ANTHROPIC_API_KEY`, then
authorize once:

```bash
.venv\Scripts\python.exe blogger.py login
```

This opens LinkedIn in your browser. After you approve, tokens are cached in `tokens.json`
(gitignored).

## Command line

Prefer the web app above (`python blogger.py serve`). The same workflow runs from the
command line, which is also what a scheduled job calls. A human is at every judgment call:

```bash
python blogger.py ingest              # build reference.md from your logs + GitHub
python blogger.py brainstorm          # Claude proposes post ideas (default 3)
python blogger.py select <n> --comment "direction"   # pick one (or: reshuffle)
python blogger.py skeleton            # gap-filled draft to write into
# fill every [YOUR VOICE: ...] gap in the draft file, then:
python blogger.py check <id>          # error check against reference.md
python blogger.py attach <id> <image> # optional media
python blogger.py approve <id> --at 2026-08-03T09:00:00-07:00   # queue for a time
python blogger.py queue               # see scheduled posts and lock state
python blogger.py publish             # publish due queued posts (safe to schedule)
```

`serve`, `list`, `show <id>`, `override <id> <flag>`, `schedule <id> --at ...`, `retry <id>`,
and `nudge` round out the commands. The legacy `draft` still does a one-shot draft from
`activity_log.md` without the brainstorm flow. Drafts default to a queue-only publish; a
draft over the word limit (default 220) cannot be approved or published from either
front-end until trimmed.

### Reminders (nudge)

The nudge is the "come write your post" email to yourself.

```bash
python blogger.py nudge            # email the reminder only if it is due
python blogger.py nudge --force    # send it now, regardless of whether it is due
python blogger.py nudge --prepare  # run ingest first so the reference is fresh
```

Without `--force`, `nudge` only emails once your posting interval (the "post every N days"
setting) has passed since your last post or nudge, so it is safe to run on a schedule and
stays quiet otherwise. Flags combine, for example `nudge --force --prepare`. The email opens
with the date of your last post (or says you have not posted yet) and points to the web app.

## Notes and limits

- **Scheduled posting.** A queued post goes live when something runs `publish` after its
  time. Click Publish now for one post, or, for hands-off publishing, schedule `publish`
  (and `nudge --prepare`) to run periodically via Task Scheduler. The nudge cadence and the
  default post spacing both follow the single "post every N days" setting.
- **Official API only.** No browser automation or scraping involved.
- **Tokens expire.** Member access tokens last about 60 days. With member-token refresh
  enabled on the app, the tool refreshes automatically; otherwise run `login` again.
- **`LINKEDIN_API_VERSION`** is a monthly `YYYYMM` value. If the API returns a 426
  `NONEXISTENT_VERSION`, bump it to a current month from LinkedIn's changelog.
- **Honesty.** The draft prompt uses only what is in your logs and is told not to invent
  results. Keep the logs accurate and the posts stay accurate.

## Privacy

Privacy policy: https://iviang.github.io/linkedin-blogger/privacy.html
