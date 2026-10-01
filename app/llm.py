"""Thin wrapper around the Claude API: structured (Pydantic) replies and long text replies."""
from collections.abc import Callable
from typing import Any, TypeVar

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


def _base_kwargs(system: str, content: str | list[dict], max_tokens: int, effort: str) -> dict:
    kwargs: dict[str, Any] = dict(
        model=config.CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
    )
    if config.CLAUDE_FALLBACKS:
        # Server-side refusal fallback, routed by refusal category.
        kwargs["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
        kwargs["extra_body"] = {"fallbacks": "default"}
    return kwargs


def _send(fn: Callable[..., Any], kwargs: dict) -> Any:
    try:
        try:
            return fn(**kwargs)
        except anthropic.BadRequestError as e:
            if "fallback" not in str(e).lower() or "extra_body" not in kwargs:
                raise
            kwargs.pop("extra_headers")
            kwargs.pop("extra_body")
            return fn(**kwargs)
    except anthropic.AuthenticationError as e:
        raise LLMError("Claude API authentication failed - set ANTHROPIC_API_KEY in .env") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Claude API rate limit hit - wait a moment and retry") from e
    except anthropic.BadRequestError as e:
        raise LLMError(f"Claude rejected the request: {e}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError(f"Could not reach the Claude API: {e}") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude API error ({e.status_code}): {e}") from e


def _check_stop(response) -> None:
    if response.stop_reason == "refusal":
        raise LLMError("Claude declined this request.")
    if response.stop_reason == "max_tokens":
        raise LLMError("Claude's reply was cut off (max_tokens). Try a shorter input.")


def call_structured(system: str, user: str | list[dict], output: type[T], effort: str = "high") -> T:
    """One Claude call whose reply is validated against `output`."""
    kwargs = _base_kwargs(system, user, 16000, effort) | {"output_format": output}
    response = _send(_get_client().messages.parse, kwargs)
    _check_stop(response)
    if response.parsed_output is None:
        raise LLMError("Claude returned a response that did not match the expected format.")
    return response.parsed_output


def call_text(system: str, content: str | list[dict], effort: str = "medium", max_tokens: int = 64000) -> str:
    """Streamed Claude call returning the reply text (for long outputs)."""
    def run(**kw):
        with _get_client().messages.stream(**kw) as stream:
            return stream.get_final_message()

    response = _send(run, _base_kwargs(system, content, max_tokens, effort))
    _check_stop(response)
    return "".join(b.text for b in response.content if b.type == "text")
