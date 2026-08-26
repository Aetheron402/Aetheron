"""
The model behind every component.

One place, so the model, the effort and the token ceiling are decided once
rather than per component, and so switching providers again later touches a
single file.

Reports run long, five to nine pages, which is why this streams: a non-streaming
request with a large max_tokens risks hitting the HTTP timeout and losing work
the user has already paid for.
"""

import logging
import os

from typing import TypeVar

import anthropic

logger = logging.getLogger(__name__)

# Anthropic's most capable model. Overridable so a cheaper tier can be selected
# from the Railway variables without a redeploy, which matters because the
# component prices are fixed and the margin is thin.
#
# Namespaced deliberately: CLAUDE_EFFORT already exists in some environments,
# and an unrelated host variable silently changing what a report costs is a
# hard fault to notice.
MODEL = os.getenv("AETHERON_MODEL", "claude-opus-5")

# How hard the model works before answering. Trades quality against tokens
# billed, so it is the main cost dial: low, medium, high, xhigh, max.
EFFORT = os.getenv("AETHERON_EFFORT", "medium")

# Reports are long. Streaming makes a ceiling this high safe.
MAX_TOKENS = int(os.getenv("AETHERON_MAX_TOKENS", "16000"))

_client = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Every component needs it; "
                "see .env.example or set it in the Railway variables."
            )
        _client = anthropic.Anthropic()
    return _client


def complete(system_blocks, user_payload: str) -> str:
    """
    Run one completion and return its text.

    `system_blocks` are ordered most stable first, so the shared house style and
    the component's own instructions can be cached across calls while the user's
    input, which changes every time, stays out of the cached prefix.
    """
    client = get_client()

    system = [{"type": "text", "text": b} for b in system_blocks if b]
    if system:
        # Cache the whole instruction prefix; only the user turn varies.
        system[-1]["cache_control"] = {"type": "ephemeral"}

    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            messages=[{"role": "user", "content": user_payload}],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.RateLimitError:
        logger.warning("Claude rate limited this request")
        raise
    except anthropic.APIStatusError as exc:
        logger.warning("Claude returned %s: %s", exc.status_code, exc.message)
        raise
    except anthropic.APIConnectionError:
        logger.warning("Could not reach the Claude API")
        raise

    # A safety decline arrives as a normal 200, so it has to be checked rather
    # than caught. Without this the caller would render an empty report and
    # charge for it.
    if message.stop_reason == "refusal":
        detail = getattr(message.stop_details, "category", None)
        raise RuntimeError(f"The model declined this request ({detail or 'unspecified'})")

    usage = message.usage
    logger.info(
        "Claude %s effort=%s in=%s out=%s cached=%s",
        MODEL, EFFORT, usage.input_tokens, usage.output_tokens,
        getattr(usage, "cache_read_input_tokens", 0),
    )

    return "".join(b.text for b in message.content if b.type == "text")


T = TypeVar("T")


def complete_structured(system_blocks, user_payload: str, schema: type[T]) -> T:
    """
    Run one completion and return it validated against `schema`.

    Where a report has known sections, asking for them as fields beats asking
    for markdown and recovering the sections with a regex afterwards. The model
    cannot merge two sections or rename one, so downstream rendering stops being
    best-effort.

    Non-streaming: a schema-constrained response is bounded by its fields rather
    than free to run long, and 16k stays inside the SDK's HTTP timeout.
    """
    client = get_client()

    system = [{"type": "text", "text": b} for b in system_blocks if b]
    if system:
        system[-1]["cache_control"] = {"type": "ephemeral"}

    message = client.messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        thinking={"type": "adaptive"},
        output_config={"effort": EFFORT},
        output_format=schema,
        messages=[{"role": "user", "content": user_payload}],
    )

    if message.stop_reason == "refusal":
        detail = getattr(message.stop_details, "category", None)
        raise RuntimeError(f"The model declined this request ({detail or 'unspecified'})")

    usage = message.usage
    logger.info(
        "Claude %s effort=%s structured in=%s out=%s cached=%s",
        MODEL, EFFORT, usage.input_tokens, usage.output_tokens,
        getattr(usage, "cache_read_input_tokens", 0),
    )

    return message.parsed_output
