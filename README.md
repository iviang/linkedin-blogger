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

**Working today**

- LinkedIn OAuth login (personal profile, `w_member_social` + OpenID Connect)
- Draft a post from a single `activity_log.md` using Claude
- Review, edit, and approve drafts locally
- Publish approved text posts through the official LinkedIn API

**Designed, not yet built**

- The AI agent log and the merged reference file (since-last-post window)
- Multi-idea brainstorm with select or reshuffle
- Skeleton draft with fill-in gaps
- The error-check loop and preview
- Media attachment
- Publish queue with the 15-minute edit lock
- The weekly "come write your post" email nudge

## Prerequisites

- **Python 3.10+**
- **A LinkedIn developer app** (personal-profile posting): Client ID and Secret, with the
  *Sign In with LinkedIn using OpenID Connect* and *Share on LinkedIn* products, and a
  `http://localhost:8000/callback` redirect URL.
- **An Anthropic API key** with a few dollars of credit.
- **A GitHub personal access token** (read-only), for the agent log. *(planned)*
- **A Gmail app password**, for the weekly email nudge over SMTP. *(planned)*
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

## Usage (current)

```bash
python blogger.py draft            # create a pending draft from your activity log
python blogger.py list             # see all drafts and their status
python blogger.py show <id>        # read one draft in full
python blogger.py approve <id>     # mark it approved
python blogger.py publish          # publish all approved drafts
```

You can edit a draft file in `drafts/` directly before approving, including changing
`status: pending` to `status: approved` by hand.

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
