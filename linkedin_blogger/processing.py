"""Stage B: brainstorm ideas, skeleton drafts with gaps, and the error-check loop.

All drafting reads reference.md (from Stage A ingest). Nothing invents facts beyond
what the reference contains.
"""

import hashlib
import json
import re
from datetime import datetime

from . import config, llm

_GAP_MARKER = re.compile(r"\[(?:YOUR|GAP)[^\]]*\]", re.IGNORECASE)
_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

_BRAINSTORM_SYSTEM = """You brainstorm LinkedIn post ideas for an AI engineering intern at \
Kool Quarters.

Rules:
- Use only facts present in the reference material. Do not invent projects, metrics, or \
outcomes.
- Each idea should be a distinct angle, not three versions of the same post.
- No em dashes in any string values.
- Return only a JSON array. No preamble, no markdown outside the array.

Each array item must have exactly these keys:
- title: short label (under 10 words)
- angle: one-sentence hook or framing for the post
- rationale: one sentence on why this fits the reference material"""


_SKELETON_SYSTEM = """You write a skeleton LinkedIn post with fill-in gaps for the owner to \
complete in their own voice.

Rules:
- Use only facts from the reference material and the chosen idea. Do not invent achievements.
- First person, warm and professional. No hype or buzzword salad.
- Leave 2 to 4 gaps marked exactly as [YOUR VOICE: brief prompt for what the owner should \
write]. Each gap prompt should be specific (for example what feeling, example, or takeaway \
to add).
- Write the fixed sentences around the gaps so the post has a clear shape (opening, body, \
close). Hashtags, if any, stay outside the gaps at the end.
- Target 120 to 220 words once gaps are filled. Plain language a hiring manager would respect.
- No em dashes. Use commas, colons, or parentheses instead.
- Return only the skeleton post text. No preamble, no quotes around it."""


_CHECK_SYSTEM = """You review a LinkedIn post draft against the reference material it was \
written from.

Flag problems in these categories only:
- discrepancy: the draft directly contradicts a fact in the reference
- grammar: clear grammatical errors
- formatting: broken structure, awkward line breaks, or unfilled gap markers left as \
[YOUR VOICE: ...]
- factualness: a concrete, checkable claim that is fabricated or contradicts the reference, \
such as invented metrics, numbers, named outcomes, or achievements the reference does not \
support
- length: under 120 words or over 220 words when gap markers are treated as filled

Do not flag the author's own interpretive or experiential content. Their motivations, \
feelings, design reasoning, process narrative (for example deciding to redesign or to keep \
more manual involvement), opinions, lessons, and reflective takeaways are theirs to make and \
cannot be verified against a commit log. The reference is a high-level summary, not an \
exhaustive spec, so a detail being absent from the reference is not by itself grounds for a \
factualness flag. Flag factualness only when a specific, checkable claim conflicts with the \
reference or is a fabricated metric or outcome that would mislead a reader. When unsure \
whether something is a subjective reflection or a factual claim, do not flag it.

Rules:
- Be specific. Each flag needs a short message and an optional excerpt from the draft.
- If the draft is clean, return passed: true with an empty flags array.
- No em dashes in message strings.
- Return only JSON with this shape:
{"passed": boolean, "flags": [{"id": "category-N", "category": "...", "message": "...", \
"excerpt": "..."}]}
- Use stable ids like grammar-1, factualness-1. Category must be one of: discrepancy, \
grammar, formatting, factualness, length."""


def _ask(system: str, user: str, max_tokens: int = 2048) -> str:
    return llm.ask(system, user, max_tokens)


def _parse_json(text: str):
    """Extract JSON from a bare string or a fenced code block."""
    match = _JSON_FENCE.search(text)
    payload = match.group(1).strip() if match else text.strip()
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        raise SystemExit(
            "The model did not return valid JSON for this step. Re-run the command; if it "
            "persists, the reference or draft may be too long for the token budget."
        )


def load_reference() -> str:
    if not config.REFERENCE_FILE.exists():
        raise SystemExit(
            f"No reference file at {config.REFERENCE_FILE}. Run: python blogger.py ingest"
        )
    text = config.REFERENCE_FILE.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"{config.REFERENCE_FILE} is empty. Run: python blogger.py ingest")
    return text


def check_path(draft_id: str):
    return config.DRAFTS_DIR / f"{draft_id}.check.json"


def load_check(draft_id: str) -> dict | None:
    path = check_path(draft_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_check(draft_id: str, result: dict) -> None:
    config.DRAFTS_DIR.mkdir(exist_ok=True)
    result = dict(result)
    result["checked_at"] = datetime.now().isoformat(timespec="seconds")
    check_path(draft_id).write_text(json.dumps(result, indent=2), encoding="utf-8")


def _body_sha(body: str) -> str:
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()


def check_is_stale(draft_id: str, body: str) -> bool:
    """True when a saved check exists but the draft body has changed since it ran."""
    check = load_check(draft_id)
    if not check or "body_sha" not in check:
        return False
    return check["body_sha"] != _body_sha(body)


def has_unfilled_gaps(body: str) -> bool:
    return bool(_GAP_MARKER.search(body))


def brainstorm_ideas(reference: str, count: int, avoid: list[dict] | None = None) -> list[dict]:
    """Return `count` post ideas as dicts with title, angle, and rationale."""
    user = f"Reference material:\n\n{reference}\n\nGenerate {count} post ideas as a JSON array."
    if avoid:
        prior = json.dumps(avoid, indent=2)
        user += (
            f"\n\nAvoid repeating these angles (generate fresh alternatives):\n{prior}"
        )
    raw = _ask(_BRAINSTORM_SYSTEM, user)
    ideas = _parse_json(raw)
    if not isinstance(ideas, list) or not ideas:
        raise SystemExit("Brainstorm returned no ideas. Try again or check the reference file.")
    return ideas[:count]


def write_skeleton(reference: str, idea: dict, comments: str = "") -> str:
    """Return a skeleton post with [YOUR VOICE: ...] gaps for the owner to fill."""
    idea_text = json.dumps(idea, indent=2)
    user = (
        f"Reference material:\n\n{reference}\n\n"
        f"Chosen idea:\n{idea_text}\n"
    )
    if comments.strip():
        user += f"\nOwner direction:\n{comments.strip()}\n"
    user += "\nWrite the skeleton post."
    return _ask(_SKELETON_SYSTEM, user, max_tokens=1024)


def _gap_flags(body: str) -> list[dict]:
    flags = []
    for i, match in enumerate(_GAP_MARKER.finditer(body), start=1):
        flags.append(
            {
                "id": f"formatting-gap-{i}",
                "category": "formatting",
                "message": "Gap marker still unfilled; replace with your own words.",
                "excerpt": match.group(0),
                "overridden": False,
            }
        )
    return flags


def run_check(reference: str, draft_body: str) -> dict:
    """Check draft against reference. Merges local gap detection with Claude review."""
    gap_flags = _gap_flags(draft_body)
    user = (
        f"Reference material:\n\n{reference}\n\n"
        f"Draft to review:\n\n{draft_body}\n\n"
        "Return the JSON review."
    )
    raw = _ask(_CHECK_SYSTEM, user, max_tokens=1024)
    result = _parse_json(raw)
    if not isinstance(result, dict) or "passed" not in result:
        raise SystemExit("Error check returned an unexpected format. Try again.")

    flags = list(result.get("flags") or [])
    seen_ids = {f.get("id") for f in flags}
    for gap in gap_flags:
        if gap["id"] not in seen_ids:
            flags.append(gap)

    passed = bool(result.get("passed")) and not gap_flags
    return {"passed": passed, "flags": flags, "body_sha": _body_sha(draft_body)}


def apply_override(draft_id: str, flag_id: str, reason: str = "") -> dict:
    """Mark one check flag as overridden by the owner."""
    check = load_check(draft_id)
    if not check:
        raise SystemExit(f"No check results for {draft_id}. Run: python blogger.py check {draft_id}")

    updated = False
    for flag in check.get("flags", []):
        if flag.get("id") == flag_id:
            flag["overridden"] = True
            if reason.strip():
                flag["override_reason"] = reason.strip()
            updated = True
            break

    if not updated:
        raise SystemExit(f"No flag with id {flag_id!r} in the latest check for {draft_id}.")

    unresolved = [f for f in check["flags"] if not f.get("overridden")]
    check["passed"] = len(unresolved) == 0
    save_check(draft_id, check)
    return check


def check_ready_for_approve(draft_id: str) -> tuple[bool, str]:
    """True when the draft passed check or every flag is overridden."""
    check = load_check(draft_id)
    if not check:
        return False, f"No error check on record. Run: python blogger.py check {draft_id}"
    if check.get("passed"):
        return True, ""
    unresolved = [f for f in check.get("flags", []) if not f.get("overridden")]
    if not unresolved:
        return True, ""
    ids = ", ".join(f["id"] for f in unresolved)
    return False, f"Unresolved check flags: {ids}. Fix the draft, override, or re-run check."


def format_ideas(ideas: list[dict]) -> str:
    lines = []
    for i, idea in enumerate(ideas, start=1):
        lines.append(f"{i}. {idea.get('title', '(untitled)')}")
        lines.append(f"   Angle: {idea.get('angle', '')}")
        lines.append(f"   Why:   {idea.get('rationale', '')}")
    return "\n".join(lines)


def format_check(check: dict) -> str:
    if check.get("passed"):
        checked = check.get("checked_at", "?")
        return f"Check passed ({checked})."

    lines = ["Check flags:"]
    for flag in check.get("flags", []):
        status = "overridden" if flag.get("overridden") else "open"
        lines.append(f"  [{status:10}] {flag.get('id')}: {flag.get('message')}")
        if flag.get("excerpt"):
            lines.append(f"             excerpt: {flag['excerpt']}")
    return "\n".join(lines)
