# LinkedIn Blogger

Transforming a week of work notes into regularly scheduled LinkedIn posts, built on AI
automation and human-computer interaction (HCI) principles.

A personal tool that drafts LinkedIn posts from your work notes and publishes the ones you
approve, on a weekly schedule. You stay the author: the automation handles the gathering,
ideating, and posting, while every judgment call stays with you. Posts go to your personal
profile through LinkedIn's official API.

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

## Status

This repo is being built up to the pipeline above. Be honest with yourself about what runs
today versus what is still designed.

**Verified at runtime**

- LinkedIn OAuth login (personal profile, `w_member_social` + OpenID Connect)
- `list`, GitHub activity preview (`activity`), and the legacy one-shot `draft` flow

**Built and compiling, not yet end-to-end tested**

- Ingestion: an AI agent log synthesized from GitHub activity plus notes, merged into
  `reference.md` for the since-last-post window (`ingest`)
- Processing: multi-idea brainstorm with select/reshuffle, skeleton-with-gaps, and the
  error-check loop (`brainstorm`, `select`, `reshuffle`, `skeleton`, `check`, `override`,
  `preview`)
- Deliverable: publish queue with the 15-minute edit lock, media attachment, scheduled
  publish, and the weekly email nudge (`approve`, `schedule`, `queue`, `attach`, `publish`,
  `nudge`)

The full flow still needs an end-to-end runtime test on a machine with dependencies
installed.

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

## Usage

The full workflow, with a human at every judgment call:

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

`list`, `show <id>`, `override <id> <flag>`, `schedule <id> --at ...`, `retry <id>`, and
`nudge` round out the commands. The legacy `draft` still does a one-shot draft from
`activity_log.md` without the brainstorm flow.

## Notes and limits

- **Official API only.** No browser automation or scraping involved.
- **Tokens expire.** Member access tokens last about 60 days. With member-token refresh
  enabled on the app, the tool refreshes automatically; otherwise run `login` again.
- **`LINKEDIN_API_VERSION`** is a monthly `YYYYMM` value. If the API returns a 426
  `NONEXISTENT_VERSION`, bump it to a current month from LinkedIn's changelog.
- **Honesty.** The draft prompt uses only what is in your logs and is told not to invent
  results. Keep the logs accurate and the posts stay accurate.

## Privacy

Privacy policy: https://iviang.github.io/linkedin-blogger/privacy.html
