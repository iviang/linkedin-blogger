"""Command-line entry point for the LinkedIn blogger.

Workflow (human stays in the loop):
  1. python blogger.py login      one-time browser authorization
  2. python blogger.py draft      read recent activity, save a PENDING draft (safe to cron)
  3. python blogger.py list       see pending / approved / posted drafts
  4. python blogger.py show <id>  read a draft's full text before deciding
  5. edit the draft file if you want, then set status to approved, or:
     python blogger.py approve <id>
  6. python blogger.py publish    post every APPROVED draft, then mark it posted

Nothing is ever published without a human setting a draft to `approved`. Running
`draft` on a schedule only ever produces pending drafts, so a cron job cannot post
on its own.
"""

import argparse
import sys
from datetime import datetime

from . import auth, config, content, github_activity, linkedin, state

# Minimal front-matter format so we avoid a YAML dependency. The block is fenced by
# lines of three dashes; everything after the closing fence is the post body.
FENCE = "---"


def _ensure_dirs():
    config.DRAFTS_DIR.mkdir(exist_ok=True)


def _write_draft(meta: dict, body: str, path):
    lines = [FENCE]
    for key, value in meta.items():
        lines.append(f"{key}: {value}")
    lines.append(FENCE)
    lines.append("")
    lines.append(body.strip())
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _read_draft(path):
    text = path.read_text(encoding="utf-8")
    meta = {}
    body = text
    if text.startswith(FENCE):
        # Split off the front-matter block between the first two fences.
        _, block, body = text.split(FENCE, 2)
        for line in block.strip().splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()
    return meta, body.strip()


def _draft_paths():
    _ensure_dirs()
    return sorted(config.DRAFTS_DIR.glob("*.md"))


def cmd_login(_args):
    auth.login()


def cmd_draft(_args):
    activity = content.recent_activity(config.DRAFT_LOOKBACK_DAYS)
    if not activity.strip():
        print("No recent activity found in the log. Nothing to draft.")
        return

    body = content.draft_post(activity)
    # Second-resolution id keeps filenames sortable and unique per run.
    draft_id = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    path = config.DRAFTS_DIR / f"{draft_id}.md"
    _ensure_dirs()
    _write_draft(
        {"id": draft_id, "status": "pending", "created": datetime.now().isoformat(timespec="seconds")},
        body,
        path,
    )
    print(f"Draft saved: {path}")
    print("Review it, then approve with:")
    print(f"  python blogger.py show {draft_id}")
    print(f"  python blogger.py approve {draft_id}")


def cmd_list(_args):
    paths = _draft_paths()
    if not paths:
        print("No drafts yet. Run: python blogger.py draft")
        return
    for path in paths:
        meta, body = _read_draft(path)
        preview = body.replace("\n", " ")[:70]
        print(f"[{meta.get('status', '?'):8}] {meta.get('id', path.stem)}  {preview}...")


def cmd_activity(_args):
    since = state.get_last_posted_at()
    window = f"since {since.date()}" if since else "recent (no prior post recorded yet)"
    repos = ", ".join(config.GITHUB_REPOS) or "(none configured, set GITHUB_REPOS in .env)"
    print(f"GitHub activity {window}")
    print(f"Repos: {repos}\n")

    data = github_activity.fetch_activity(since)

    print(f"Commits ({len(data['commits'])}):")
    for c in data["commits"]:
        print(f"  [{c['repo']}] {c['date']}  {c['message']}")
    print(f"\nPull requests ({len(data['pulls'])}):")
    for p in data["pulls"]:
        print(f"  [{p['repo']}] #{p['number']} ({p['state']})  {p['title']}")
    if not data["commits"] and not data["pulls"]:
        print("\nNo activity found. Check GITHUB_REPOS and that the token can read them.")


def _find_draft(draft_id: str):
    path = config.DRAFTS_DIR / f"{draft_id}.md"
    if not path.exists():
        raise SystemExit(f"No draft with id {draft_id}. Run: python blogger.py list")
    return path


def cmd_show(args):
    path = _find_draft(args.id)
    meta, body = _read_draft(path)
    print(f"id:     {meta.get('id')}")
    print(f"status: {meta.get('status')}")
    print(f"file:   {path}")
    print("-" * 60)
    print(body)


def cmd_approve(args):
    path = _find_draft(args.id)
    meta, body = _read_draft(path)
    if meta.get("status") == "posted":
        raise SystemExit("That draft is already posted.")
    meta["status"] = "approved"
    _write_draft(meta, body, path)
    print(f"Approved {args.id}. It will publish on the next: python blogger.py publish")


def cmd_publish(_args):
    published = 0
    for path in _draft_paths():
        meta, body = _read_draft(path)
        if meta.get("status") != "approved":
            continue
        print(f"Publishing {meta.get('id')} ...")
        urn = linkedin.publish_text_post(body)
        meta["status"] = "posted"
        meta["posted_urn"] = urn
        meta["posted_at"] = datetime.now().isoformat(timespec="seconds")
        _write_draft(meta, body, path)
        print(f"  posted as {urn}")
        published += 1

    if published == 0:
        print("No approved drafts to publish. Approve one first: python blogger.py approve <id>")
    else:
        print(f"Done. Published {published} post(s).")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Draft and publish LinkedIn posts from an activity log.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="Authorize with LinkedIn in the browser (one time).").set_defaults(func=cmd_login)
    sub.add_parser("draft", help="Generate a pending draft from recent activity.").set_defaults(func=cmd_draft)
    sub.add_parser("list", help="List all drafts and their status.").set_defaults(func=cmd_list)
    sub.add_parser("activity", help="Preview GitHub activity since your last post.").set_defaults(func=cmd_activity)

    show = sub.add_parser("show", help="Print one draft in full.")
    show.add_argument("id")
    show.set_defaults(func=cmd_show)

    approve = sub.add_parser("approve", help="Mark a draft approved so publish will post it.")
    approve.add_argument("id")
    approve.set_defaults(func=cmd_approve)

    sub.add_parser("publish", help="Publish every approved draft.").set_defaults(func=cmd_publish)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
