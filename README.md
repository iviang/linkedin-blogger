# LinkedIn blogger

Drafts LinkedIn posts summarizing your intern work at Kool Quarters, and publishes the
ones you approve. Posts go to your **personal profile** through LinkedIn's official
Posts API. Nothing publishes without you approving it first.

## How it works

1. You keep `activity_log.md` up to date with what you worked on, under dated headings.
2. `draft` reads the last few days of entries and uses Claude to write a post, saved as a
   `pending` draft under `drafts/`.
3. You read the draft, edit it if you like, and `approve` it.
4. `publish` posts every `approved` draft and marks it `posted`.

A scheduled `draft` job can only ever create pending drafts, so automation never posts on
its own. Approval is the human gate.

## One-time setup

### 1. Create a LinkedIn app

Go to https://www.linkedin.com/developers/apps and create an app.

- On the **Products** tab, request **Share on LinkedIn** and **Sign In with LinkedIn
  using OpenID Connect**. These grant the `w_member_social`, `openid`, and `profile`
  scopes this tool needs. Approval for these is usually quick.
- On the **Auth** tab, add an authorized redirect URL: `http://localhost:8000/callback`
  (must match `LINKEDIN_REDIRECT_URI` exactly).
- Copy the Client ID and Client Secret.

Posting to a personal profile does not need the heavier Community Management API. If you
later want to post to a Company Page instead, that is a different product with a stricter
LinkedIn review, and this tool would need changes to the author URN and scopes.

### 2. Configure the environment

```bash
cd linkedin-blogger
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

cp .env.example .env                       # then edit .env with your real keys
cp activity_log.example.md activity_log.md # your working log (gitignored)
```

Fill in `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, and `ANTHROPIC_API_KEY`.

### 3. Authorize

```bash
python blogger.py login
```

This opens LinkedIn in your browser. After you approve, tokens are cached in
`tokens.json` (gitignored).

## Daily use

```bash
python blogger.py draft            # create a pending draft from recent activity
python blogger.py list             # see all drafts and their status
python blogger.py show <id>        # read one draft in full
python blogger.py approve <id>     # mark it approved
python blogger.py publish          # publish all approved drafts
```

You can also edit the draft file in `drafts/` directly before approving, including
changing `status: pending` to `status: approved` by hand.

## Scheduling (safe part only)

Automate `draft` so a fresh draft is waiting for you, but keep `publish` manual (or gate
it behind your own review). On Windows, use Task Scheduler to run, for example every
Monday at 9am:

```
python C:\Vivian\Documents\KOOL\linkedin-blogger\blogger.py draft
```

On Linux/macOS cron, the same idea:

```
0 9 * * 1 cd /path/to/linkedin-blogger && .venv/bin/python blogger.py draft
```

To be notified when a draft is ready, have the scheduled job also email you. Ask if you
want that wired in.

## Notes and limits

- **Official API only.** No browser automation or scraping. That would break LinkedIn's
  User Agreement and put your account at risk.
- **Tokens expire.** Member access tokens last about 60 days. If your app has member-token
  refresh enabled, the tool refreshes automatically; otherwise run `login` again.
- **`LINKEDIN_API_VERSION`** is a monthly value (YYYYMM). If the API rejects the version,
  bump it to a current month from LinkedIn's changelog.
- **Honesty.** The draft prompt is told to use only what is in your activity log and not
  to invent results. Keep the log accurate and the posts stay accurate.
- Text-only posts for now. Adding an image or a link preview is a straightforward
  extension if you want it.
```
