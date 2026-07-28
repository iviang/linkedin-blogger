"""Shared Anthropic client: one place for the client, retries, transient-error handling,
and text extraction, so the drafting modules do not each repeat it.
"""

import anthropic

from . import config

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
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.OverloadedError:
        raise SystemExit("Anthropic is temporarily overloaded (529). Wait a moment and re-run.")
    except anthropic.RateLimitError:
        raise SystemExit("Hit the Anthropic rate limit (429). Wait a moment and re-run.")
    # Sonnet 5 uses adaptive thinking, so content[0] can be a ThinkingBlock; take the first
    # text block rather than assuming position 0.
    return next((b.text for b in message.content if b.type == "text"), "").strip()
