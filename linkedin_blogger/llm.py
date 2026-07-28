"""Shared Anthropic client: one place for the client, retries, transient-error handling,
and text extraction, so the drafting modules do not each repeat it.
"""

import base64

import anthropic

from . import config

_ALT_SYSTEM = """You write alt text for photos posted on LinkedIn, read aloud to screen \
reader users.

Rules:
- One sentence, factual and specific about the meaningful content.
- Aim for under 125 characters.
- Do not begin with "image of", "photo of", or "a picture of". Describe the content directly.
- For a chart or diagram, say what it shows, not every label.
- No em dashes. No emoji. Straight quotes only.
- Return only the alt text, with no surrounding quotes."""

# Retry transient overload (529), rate limit (429), and 5xx a bit more than the SDK
# default of 2, so a short overload rides out instead of surfacing as a traceback.
_MAX_RETRIES = 5


def _client() -> anthropic.Anthropic:
    config.require("ANTHROPIC_API_KEY", config.ANTHROPIC_API_KEY)
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=_MAX_RETRIES)


def ask(system: str, user: str, max_tokens: int = 2048) -> str:
    """Send one prompt and return the first text block, with clean transient-error messages."""
    try:
        message = _client().messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system,
            # These are deterministic drafting/extraction tasks. Sonnet 5 runs adaptive
            # thinking by default, which can consume the whole max_tokens budget and leave
            # the text output empty (JSON checks then fail). Turn it off so the full budget
            # goes to the answer.
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.OverloadedError:
        raise SystemExit("Anthropic is temporarily overloaded (529). Wait a moment and re-run.")
    except anthropic.RateLimitError:
        raise SystemExit("Hit the Anthropic rate limit (429). Wait a moment and re-run.")
    # Sonnet 5 uses adaptive thinking, so content[0] can be a ThinkingBlock; take the first
    # text block rather than assuming position 0.
    return next((b.text for b in message.content if b.type == "text"), "").strip()


def describe_image(image_bytes: bytes, media_type: str) -> str:
    """Return concise alt text for an image using Claude's vision. Transient errors raise
    SystemExit like ask(), so callers can surface a clean message."""
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    try:
        message = _client().messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=200,
            system=_ALT_SYSTEM,
            thinking={"type": "disabled"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": b64},
                        },
                        {"type": "text", "text": "Write alt text for this image."},
                    ],
                }
            ],
        )
    except anthropic.OverloadedError:
        raise SystemExit("Anthropic is temporarily overloaded (529). Wait a moment and re-run.")
    except anthropic.RateLimitError:
        raise SystemExit("Hit the Anthropic rate limit (429). Wait a moment and re-run.")
    except anthropic.APIError as exc:
        # A bad or unreadable image comes back as a 400; surface it cleanly rather than 500.
        raise SystemExit(f"Could not describe this image: {getattr(exc, 'message', str(exc))}")
    return next((b.text for b in message.content if b.type == "text"), "").strip()
