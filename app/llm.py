"""Thin wrapper around the Claude API for structured (Pydantic) responses."""
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from . import config

T = TypeVar("T", bound=BaseModel)

_client: anthropic.Anthropic | None = None


class LLMError(RuntimeError):
    pass


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def call_structured(system: str, user: str, output: type[T], effort: str = "high") -> T:
    """One Claude call whose reply is validated against `output`."""
    kwargs = dict(
        model=config.CLAUDE_MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        output_format=output,
    )
    if config.CLAUDE_FALLBACKS:
        # Server-side refusal fallback, routed by refusal category.
        kwargs["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
        kwargs["extra_body"] = {"fallbacks": "default"}

    client = _get_client()
    try:
        response = client.messages.parse(**kwargs)
    except anthropic.BadRequestError as e:
        if "fallback" not in str(e).lower() or "extra_body" not in kwargs:
            raise LLMError(f"Claude rejected the request: {e}") from e
        kwargs.pop("extra_headers")
        kwargs.pop("extra_body")
        response = client.messages.parse(**kwargs)
    except anthropic.AuthenticationError as e:
        raise LLMError("Claude API authentication failed - set ANTHROPIC_API_KEY in .env") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Claude API rate limit hit - wait a moment and retry") from e
    except anthropic.APIConnectionError as e:
        raise LLMError(f"Could not reach the Claude API: {e}") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude API error ({e.status_code}): {e}") from e

    if response.stop_reason == "refusal":
        raise LLMError("Claude declined this request.")
    if response.stop_reason == "max_tokens":
        raise LLMError("Claude's reply was cut off (max_tokens). Try a shorter job description.")
    if response.parsed_output is None:
        raise LLMError("Claude returned a response that did not match the expected format.")
    return response.parsed_output
