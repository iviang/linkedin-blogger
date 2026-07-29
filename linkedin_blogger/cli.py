"""Command-line entry point for the LinkedIn blogger.

Stage B workflow (human stays in the loop):
  1. python blogger.py ingest       build reference.md from logs + GitHub
  2. python blogger.py brainstorm   Claude proposes post ideas (default 3)
  3. python blogger.py select <n>   pick an idea; optional --comment for direction
     python blogger.py reshuffle     new ideas if none fit
  4. python blogger.py skeleton     write a gap-filled skeleton draft
  5. edit the draft file, fill every [YOUR VOICE: ...] marker
  6. python blogger.py check <id>   error check against reference.md
     python blogger.py override <id> <flag>  dismiss a flag you accept
  7. python blogger.py preview <id> read draft + check results
  8. python blogger.py approve <id> [--at ISO8601]  queue for publishing
     python blogger.py attach <id> <image>  optional media (jpg/png/gif)
  9. python blogger.py queue            see scheduled posts and lock state
 10. python blogger.py publish           post due queued drafts (cron-friendly)
     python blogger.py nudge [--prepare] weekly email reminder

Legacy shortcut: `draft` still drafts directly from activity_log.md (no brainstorm).

Nothing publishes without human approval.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import agent_log, auth, config, content, drafts, github_activity, nudge, processing, queue, state

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


def _new_draft_id() -> str:
    return datetime.now().strftime("%Y-%m-%d-%H%M%S")


def _find_draft(draft_id: str):
    path = config.DRAFTS_DIR / f"{draft_id}.md"
    if not path.exists():
        raise SystemExit(f"No draft with id {draft_id}. Run: python blogger.py list")
    return path


def _load_session_ideas() -> list[dict]:
    ideas = state.get_processing().get("ideas") or []
    if not ideas:
        raise SystemExit("No ideas in session. Run: python blogger.py brainstorm")
    return ideas


def cmd_login(_args):
    auth.login()


def cmd_draft(_args):
    activity = content.recent_activity(config.DRAFT_LOOKBACK_DAYS)
    if not activity.strip():
        print("No recent activity found in the log. Nothing to draft.")
        return

    body = content.draft_post(activity)
    draft_id = _new_draft_id()
    path = config.DRAFTS_DIR / f"{draft_id}.md"
    _ensure_dirs()
    _write_draft(
        {"id": draft_id, "status": "pending", "created": datetime.now().isoformat(timespec="seconds")},
        body,
        path,
    )
    print(f"Draft saved: {path}")
    print("Legacy one-shot draft (no brainstorm). Prefer: ingest -> brainstorm -> skeleton.")
    print(f"  python blogger.py show {draft_id}")
    print(f"  python blogger.py approve {draft_id}")


def cmd_brainstorm(args):
    reference = processing.load_reference()
    count = args.count or config.BRAINSTORM_IDEA_COUNT
    ideas = processing.brainstorm_ideas(reference, count)
    state.save_processing({"ideas": ideas, "selected_index": None, "comments": ""})
    print(f"Brainstormed {len(ideas)} ideas:\n")
    print(processing.format_ideas(ideas))
    print("\nPick one with:")
    print("  python blogger.py select <number> [--comment \"your direction\"]")


def cmd_ideas(_args):
    ideas = _load_session_ideas()
    session = state.get_processing()
    print(processing.format_ideas(ideas))
    selected = session.get("selected_index")
    if selected is not None:
        print(f"\nSelected: #{selected + 1}")
        if session.get("comments"):
            print(f"Comments: {session['comments']}")


def cmd_select(args):
    ideas = _load_session_ideas()
    index = args.number - 1
    if index < 0 or index >= len(ideas):
        raise SystemExit(f"Pick a number from 1 to {len(ideas)}.")

    session = state.get_processing()
    session["selected_index"] = index
    session["comments"] = args.comment or ""
    state.save_processing(session)

    idea = ideas[index]
    print(f"Selected idea {args.number}: {idea.get('title', '')}")
    if session["comments"]:
        print(f"Direction: {session['comments']}")
    print("\nNext:")
    print("  python blogger.py skeleton")


def cmd_reshuffle(args):
    reference = processing.load_reference()
    session = state.get_processing()
    prior = session.get("ideas") or []
    count = args.count or config.BRAINSTORM_IDEA_COUNT
    ideas = processing.brainstorm_ideas(reference, count, avoid=prior or None)
    state.save_processing({"ideas": ideas, "selected_index": None, "comments": ""})
    print(f"New ideas ({len(ideas)}):\n")
    print(processing.format_ideas(ideas))
    print("\nPick one with:")
    print("  python blogger.py select <number>")


def cmd_skeleton(_args):
    session = state.get_processing()
    ideas = _load_session_ideas()
    selected = session.get("selected_index")
    if selected is None:
        raise SystemExit("No idea selected. Run: python blogger.py select <number>")

    reference = processing.load_reference()
    idea = ideas[selected]
    body = processing.write_skeleton(reference, idea, session.get("comments", ""))

    draft_id = _new_draft_id()
    path = config.DRAFTS_DIR / f"{draft_id}.md"
    _ensure_dirs()
    _write_draft(
        {
            "id": draft_id,
            "status": "skeleton",
            "flow": "stage_b",
            "created": datetime.now().isoformat(timespec="seconds"),
            "idea_title": idea.get("title", ""),
        },
        body,
        path,
    )
    print(f"Skeleton saved: {path}")
    print("Fill every [YOUR VOICE: ...] gap in your own words, then run:")
    print(f"  python blogger.py check {draft_id}")


def cmd_check(args):
    path = _find_draft(args.id)
    meta, body = _read_draft(path)
    if meta.get("status") == "posted":
        raise SystemExit("That draft is already posted.")
    queue.assert_editable(meta)

    reference = processing.load_reference()
    result = processing.run_check(reference, body)
    processing.save_check(args.id, result)
    print(processing.format_check(result))

    if result["passed"]:
        if meta.get("status") == "skeleton":
            meta["status"] = "pending"
            _write_draft(meta, body, path)
        print(f"\nDraft {args.id} is ready to approve.")
    else:
        print("\nFix the draft or override a flag you accept:")
        print(f"  python blogger.py override {args.id} <flag_id>")
        print(f"  python blogger.py check {args.id}   (after edits)")


def cmd_override(args):
    check = processing.apply_override(args.id, args.flag_id, args.reason or "")
    print(processing.format_check(check))
    if check.get("passed"):
        path = _find_draft(args.id)
        meta, body = _read_draft(path)
        if meta.get("status") == "skeleton":
            meta["status"] = "pending"
            _write_draft(meta, body, path)
        print(f"\nAll flags resolved. Draft {args.id} is ready to approve.")


def cmd_preview(args):
    path = _find_draft(args.id)
    meta, body = _read_draft(path)
    print(f"id:     {meta.get('id')}")
    print(f"status: {meta.get('status')}")
    print(f"file:   {path}")
    if meta.get("scheduled_at"):
        scheduled = queue.parse_scheduled_at(meta["scheduled_at"])
        print(f"scheduled: {scheduled.isoformat(timespec='seconds')}")
        if queue.is_locked(scheduled):
            print(f"locked: edits blocked ({config.QUEUE_LOCK_MINUTES} min before publish)")
        else:
            print(f"editable until: {queue.lock_deadline(scheduled).isoformat(timespec='seconds')}")
    media = drafts.get_media(meta)
    if media:
        print(f"media:  {', '.join(m['path'] for m in media)}")
    if meta.get("publish_error"):
        print(f"last error: {meta['publish_error']}")
    check = processing.load_check(args.id)
    if check:
        if processing.check_is_stale(args.id, body):
            print(f"Note: draft changed since this check. Re-run: python blogger.py check {args.id}")
        print(processing.format_check(check))
    elif meta.get("flow") == "stage_b":
        print("No error check yet. Run: python blogger.py check", args.id)
    print("-" * 60)
    print(body)


def cmd_list(_args):
    paths = _draft_paths()
    if not paths:
        print("No drafts yet. Run: python blogger.py brainstorm")
        return
    for path in paths:
        meta, body = _read_draft(path)
        preview = body.replace("\n", " ")[:70]
        print(queue.format_queue_line(meta, preview))


def cmd_queue(_args):
    paths = _draft_paths()
    queued = []
    for path in paths:
        meta, body = _read_draft(path)
        if meta.get("status") in ("queued", "approved", "failed"):
            queued.append((meta, body))
    if not queued:
        print("Queue is empty. Approve a draft with: python blogger.py approve <id> --at <time>")
        return
    for meta, body in queued:
        preview = body.replace("\n", " ")[:70]
        print(queue.format_queue_line(meta, preview))


def cmd_ingest(_args):
    since, agent_text, _reference = agent_log.run_ingest()
    window = f"since {since.date()}" if since else "all available history"
    print(f"Ingestion window: {window}")
    print(f"Wrote {config.AGENT_LOG}")
    print(f"Wrote {config.REFERENCE_FILE}")
    print("\nAgent log preview:")
    print("-" * 60)
    preview = agent_text.replace("\n", " ")[:280]
    print(preview + ("..." if len(agent_text) > 280 else ""))
    print("\nNext: python blogger.py brainstorm")


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


def cmd_show(args):
    cmd_preview(args)


def cmd_approve(args):
    path = _find_draft(args.id)
    meta, body = _read_draft(path)
    if meta.get("status") == "posted":
        raise SystemExit("That draft is already posted.")
    if meta.get("status") == "posting":
        raise SystemExit("That draft is publishing right now. Wait for it to finish.")
    if meta.get("status") == "skeleton":
        raise SystemExit(
            f"Draft {args.id} is still a skeleton. Fill the gaps and run: "
            f"python blogger.py check {args.id}"
        )
    if meta.get("flow") == "stage_b":
        if processing.check_is_stale(args.id, body):
            raise SystemExit(
                f"Draft {args.id} changed since its last check. Re-run: "
                f"python blogger.py check {args.id}"
            )
        ok, message = processing.check_ready_for_approve(args.id)
        if not ok:
            raise SystemExit(message)

    queue.assert_editable(meta)

    words = len(body.split())
    if words > config.MAX_POST_WORDS:
        raise SystemExit(
            f"Draft is {words} words, over the {config.MAX_POST_WORDS}-word limit. Trim it before queuing."
        )

    if args.at:
        scheduled = queue.parse_scheduled_at(args.at)
        queue.assert_min_lead(scheduled)
    else:
        # No time given: space it one interval after the queue (or the last post), matching
        # the web UI. With nothing scheduled yet, that is one interval from now, never now.
        scheduled = queue.default_schedule(args.id)

    meta.update(queue.queue_meta(scheduled))
    _write_draft(meta, body, path)

    deadline = queue.lock_deadline(scheduled)
    print(f"Queued {args.id} for {scheduled.isoformat(timespec='seconds')}")
    print(f"Editable until {deadline.isoformat(timespec='seconds')} ({config.QUEUE_LOCK_MINUTES} min before).")
    print("Due posts publish on: python blogger.py publish")


def cmd_schedule(args):
    path = _find_draft(args.id)
    meta, body = _read_draft(path)
    if meta.get("status") not in ("queued", "approved", "failed"):
        raise SystemExit("Only queued drafts can be rescheduled.")
    queue.assert_editable(meta)

    scheduled = queue.parse_scheduled_at(args.at)
    queue.assert_min_lead(scheduled)
    meta.update(queue.queue_meta(scheduled))
    _write_draft(meta, body, path)
    print(f"Rescheduled {args.id} for {scheduled.isoformat(timespec='seconds')}")


def cmd_unqueue(args):
    path = _find_draft(args.id)
    meta, body = _read_draft(path)
    if meta.get("status") not in ("queued", "approved", "failed"):
        raise SystemExit("Only a queued draft can be removed from the queue.")
    queue.assert_editable(meta)
    meta["status"] = "pending"
    meta.pop("scheduled_at", None)
    meta.pop("publish_error", None)
    meta.pop("failed_at", None)
    _write_draft(meta, body, path)
    print(f"Removed {args.id} from the queue. It is back in your drafts, unscheduled.")


def cmd_attach(args):
    path = _find_draft(args.id)
    meta, body = _read_draft(path)
    queue.assert_editable(meta)

    candidate = Path(args.path)
    if not candidate.is_absolute():
        candidate = config.BASE_DIR / candidate
    if not candidate.exists():
        raise SystemExit(f"Media file not found: {candidate}")

    try:
        rel = candidate.relative_to(config.BASE_DIR).as_posix()
    except ValueError:
        rel = str(candidate)

    media = drafts.get_media(meta)
    media.append({"path": rel, "alt": args.alt or ""})
    drafts.set_media(meta, media)
    _write_draft(meta, body, path)
    print(f"Attached {rel} to {args.id} ({len(media)} photo(s) total)")


def cmd_retry(args):
    path = _find_draft(args.id)
    meta, body = _read_draft(path)
    if meta.get("status") != "failed":
        raise SystemExit("Only failed drafts can be retried.")
    prior_error = meta.get("publish_error", "")
    meta["status"] = "queued"
    meta.pop("publish_error", None)
    meta.pop("failed_at", None)
    _write_draft(meta, body, path)
    print(f"Reset {args.id} to queued. Run publish when due.")
    if "unknown outcome" in prior_error:
        print("Warning: the previous attempt had an unknown outcome. Confirm this post is NOT")
        print("already live on LinkedIn before publishing again, to avoid a duplicate.")


def cmd_publish(_args):
    published = 0
    failed = 0
    for path in _draft_paths():
        meta, body = _read_draft(path)
        if not queue.ready_to_publish(meta):
            continue

        draft_id = meta.get("id", path.stem)
        print(f"Publishing {draft_id} ...")

        def write_draft(updated_meta, updated_body):
            _write_draft(updated_meta, updated_body, path)

        if queue.publish_draft(meta, body, write_draft):
            published += 1
        else:
            failed += 1

    if published == 0 and failed == 0:
        print("No due drafts to publish. Queue one with: python blogger.py approve <id> --at <time>")
    else:
        print(f"Done. Published {published}, failed {failed}.")


def cmd_nudge(args):
    nudge.send_nudge(prepare=args.prepare, force=args.force)


def cmd_serve(args):
    # Imported lazily so the CLI works without Flask installed unless you use the web UI.
    from . import web
    web.run(port=args.port, open_browser=not args.no_browser)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Draft and publish LinkedIn posts from work notes.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="Authorize with LinkedIn in the browser (one time).").set_defaults(func=cmd_login)
    sub.add_parser("draft", help="Legacy: one-shot draft from activity_log.md.").set_defaults(func=cmd_draft)
    sub.add_parser("list", help="List all drafts and their status.").set_defaults(func=cmd_list)
    sub.add_parser(
        "ingest",
        help="Synthesize agent_log.md and reference.md since your last post.",
    ).set_defaults(func=cmd_ingest)
    sub.add_parser("activity", help="Preview GitHub activity since your last post.").set_defaults(func=cmd_activity)

    brainstorm = sub.add_parser("brainstorm", help="Brainstorm post ideas from reference.md.")
    brainstorm.add_argument(
        "--count",
        type=int,
        default=None,
        help=f"Number of ideas (default {config.BRAINSTORM_IDEA_COUNT} from .env).",
    )
    brainstorm.set_defaults(func=cmd_brainstorm)

    sub.add_parser("ideas", help="Show brainstorm ideas from the current session.").set_defaults(func=cmd_ideas)

    select = sub.add_parser("select", help="Select a brainstorm idea by number.")
    select.add_argument("number", type=int, help="Idea number from brainstorm output (1-based).")
    select.add_argument("--comment", default="", help="Optional direction for the skeleton draft.")
    select.set_defaults(func=cmd_select)

    reshuffle = sub.add_parser("reshuffle", help="Generate fresh brainstorm ideas.")
    reshuffle.add_argument("--count", type=int, default=None, help="Number of ideas.")
    reshuffle.set_defaults(func=cmd_reshuffle)

    sub.add_parser("skeleton", help="Write a gap-filled skeleton from the selected idea.").set_defaults(
        func=cmd_skeleton
    )

    check = sub.add_parser("check", help="Run the error check on a draft.")
    check.add_argument("id")
    check.set_defaults(func=cmd_check)

    override = sub.add_parser("override", help="Override one error-check flag.")
    override.add_argument("id")
    override.add_argument("flag_id", help="Flag id from check output (e.g. grammar-1).")
    override.add_argument("--reason", default="", help="Why you are accepting this flag.")
    override.set_defaults(func=cmd_override)

    preview = sub.add_parser("preview", help="Show a draft and its check results.")
    preview.add_argument("id")
    preview.set_defaults(func=cmd_preview)

    show = sub.add_parser("show", help="Print one draft in full (alias for preview).")
    show.add_argument("id")
    show.set_defaults(func=cmd_show)

    approve = sub.add_parser("approve", help="Queue a draft for scheduled publishing.")
    approve.add_argument("id")
    approve.add_argument(
        "--at",
        default="",
        help="When to publish (ISO datetime). Default: one posting interval after the queue "
        "(or the last post); a same-day time must be at least 30 minutes out.",
    )
    approve.set_defaults(func=cmd_approve)

    schedule = sub.add_parser("schedule", help="Change when a queued draft publishes.")
    schedule.add_argument("id")
    schedule.add_argument("--at", required=True, help="New publish time (ISO datetime).")
    schedule.set_defaults(func=cmd_schedule)

    unqueue = sub.add_parser("unqueue", help="Remove a queued draft from the queue (back to drafts).")
    unqueue.add_argument("id")
    unqueue.set_defaults(func=cmd_unqueue)

    attach = sub.add_parser("attach", help="Attach an image to a queued draft (jpg/png/gif).")
    attach.add_argument("id")
    attach.add_argument("path", help="Image path relative to repo root, or absolute.")
    attach.add_argument("--alt", default="", help="Alt text for accessibility.")
    attach.set_defaults(func=cmd_attach)

    retry = sub.add_parser("retry", help="Reset a failed draft back to queued.")
    retry.add_argument("id")
    retry.set_defaults(func=cmd_retry)

    sub.add_parser("queue", help="List drafts waiting to publish.").set_defaults(func=cmd_queue)
    sub.add_parser("publish", help="Publish due queued drafts (safe to cron).").set_defaults(func=cmd_publish)

    nudge_cmd = sub.add_parser(
        "nudge",
        help="Email a reminder if a post is due (your post-interval setting since the last "
        "post or nudge). Safe to run on a schedule.",
    )
    nudge_cmd.add_argument(
        "--prepare",
        action="store_true",
        help="Run ingest before sending so reference.md is fresh.",
    )
    nudge_cmd.add_argument(
        "--force",
        action="store_true",
        help="Send it now, regardless of whether it is due.",
    )
    nudge_cmd.set_defaults(func=cmd_nudge)

    serve = sub.add_parser("serve", help="Run the local web interface at localhost.")
    serve.add_argument("--port", type=int, default=5000, help="Port to serve on (default 5000).")
    serve.add_argument("--no-browser", action="store_true", help="Do not open a browser window.")
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
