"""Anthropic Messages API adapter with the same structured() surface as ChatClient.

Fixed Anthropic host and a server-only key from backend/.env; VLM_BASE_URL is ignored so the
key is never sent to another endpoint. Install with `uv sync --extra claude`.
"""

from __future__ import annotations

import re
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from ..config import settings

T = TypeVar("T", bound=BaseModel)

# Models that accept server-side refusal fallbacks in their "default" form.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
# Adaptive thinking shares max_tokens with the answer, so short caller limits would truncate.
MIN_MAX_TOKENS = 8000

# Calls made in the current analysis run; analyze resets it. Capped by AI_MAX_CALLS so a video can't run away.
usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}


def reset_usage() -> None:
    usage.update(calls=0, input_tokens=0, output_tokens=0)


class ClaudeError(RuntimeError):
    pass


def _content(content) -> str | list[dict]:
    if isinstance(content, str):
        return content
    blocks = []
    for item in content:
        if item.get("type") == "text":
            blocks.append({"type": "text", "text": item["text"]})
        elif item.get("type") == "image_url":
            match = re.fullmatch(r"data:(image/(?:jpeg|png));base64,([A-Za-z0-9+/=]+)", item["image_url"]["url"])
            if not match:
                raise ClaudeError("Only original inline image evidence is supported.")
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": match[1], "data": match[2]}})
    return blocks


def convert(messages: list[dict]) -> tuple[str, list[dict]]:
    """OpenAI-style messages -> (system prompt, Anthropic messages)."""
    system = [m["content"] for m in messages if m["role"] == "system"]
    turns = [{"role": m["role"], "content": _content(m["content"])} for m in messages if m["role"] != "system"]
    return "\n\n".join(system), turns


class ClaudeClient:
    def __init__(self, api_key: str, model: str, *, client=None):
        if not client and (not api_key or api_key == "EMPTY"):
            raise ClaudeError("Video analysis is not configured. Contact the administrator.")
        self.model = model
        self.client = client or anthropic.Anthropic(api_key=api_key, timeout=300)

    def _request(self, messages: list[dict], schema: type[T], max_tokens: int):
        system, turns = convert(messages)
        kwargs = {"model": self.model, "max_tokens": max(max_tokens, MIN_MAX_TOKENS),
                  "messages": turns, "output_format": schema,
                  "output_config": {"effort": "low"}}  # describing frames needs little reasoning; keeps output cost low
        if system:
            kwargs["system"] = system
        if self.model in FALLBACK_MODELS:
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        try:
            return self.client.beta.messages.parse(**kwargs)
        except anthropic.AuthenticationError:
            raise ClaudeError("Anthropic did not accept this API key.") from None
        except anthropic.PermissionDeniedError:
            raise ClaudeError("This Anthropic key does not have permission for the selected model.") from None
        except anthropic.NotFoundError:
            raise ClaudeError("Anthropic does not recognise the configured model. Check VLM_MODEL.") from None
        except anthropic.RateLimitError:
            raise ClaudeError("Anthropic rate limit reached. Retry later.") from None
        except anthropic.BadRequestError as exc:
            if "credit balance" in str(exc).lower():
                raise ClaudeError("Anthropic account credit is too low. Add credit in the Claude Console.") from None
            raise ClaudeError(f"Anthropic rejected the request: {exc.message}") from None
        except anthropic.APIConnectionError:
            raise ClaudeError("Anthropic could not be reached. Check network access and retry.") from None
        except anthropic.APIStatusError:
            raise ClaudeError("Anthropic generation is temporarily unavailable. Retry later.") from None

    def structured(self, messages: list[dict], schema: type[T], *, retries: int = 1,
                   max_tokens: int = 4096, **_ignored) -> T:
        """Schema-validated output. Sampling options such as temperature are not sent; current models reject them."""
        last_err = ""
        for _ in range(retries + 1):
            if usage["calls"] >= settings.ai_max_calls:
                raise ClaudeError(f"AI call budget ({settings.ai_max_calls}) used up. Raise AI_MAX_CALLS in backend/.env.")
            usage["calls"] += 1
            msgs = messages if not last_err else messages + [
                {"role": "user", "content": f"Your previous answer was invalid: {last_err[:800]}. "
                                            "Reply again with only valid JSON matching the schema."}]
            try:
                response = self._request(msgs, schema, max_tokens)
            except ValidationError as exc:
                last_err = str(exc)
                continue
            usage["input_tokens"] += response.usage.input_tokens
            usage["output_tokens"] += response.usage.output_tokens
            if response.stop_reason == "refusal":
                raise ClaudeError("Claude declined to analyse this content.")
            if response.stop_reason == "max_tokens":
                raise ClaudeError("Claude did not return a complete answer (output limit). Retry with a shorter clip.")
            if response.parsed_output is None:
                last_err = "no JSON answer was returned"
                continue
            return response.parsed_output
        raise ClaudeError(f"Claude output did not match the required evidence format: {last_err[:300]}")
