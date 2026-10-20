"""Mocked Anthropic SDK contracts only. These tests never call the live API or use a real key."""
import base64
from dataclasses import replace
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from pydantic import BaseModel

from app.providers import anthropic_client as claude
from app.providers import vlm


class Answer(BaseModel):
    connected: bool


class FakeMessages:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, str):  # raw model text -> parse it the way the SDK does
            parsed = kwargs["output_format"].model_validate_json(result)
            result = _response(parsed)
        return result


def _response(parsed, stop_reason="end_turn"):
    return SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason,
                           usage=SimpleNamespace(input_tokens=7, output_tokens=3))


def _client(*results, model="claude-opus-5-5"):
    messages = FakeMessages(*results)
    return claude.ClaudeClient("unused", model, client=SimpleNamespace(beta=SimpleNamespace(messages=messages))), messages


def _image_message():
    url = "data:image/jpeg;base64," + base64.b64encode(b"fixture").decode()
    return [{"role": "system", "content": "Observe only."},
            {"role": "user", "content": [{"type": "text", "text": "Frame"}, {"type": "image_url", "image_url": {"url": url}}]}]


def test_converts_openai_messages_and_counts_usage():
    claude.reset_usage()
    c, messages = _client('{"connected": true}')
    assert c.structured(_image_message(), Answer, temperature=0.0, max_tokens=512).connected
    sent = messages.calls[0]
    assert sent["system"] == "Observe only." and sent["output_format"] is Answer
    assert sent["messages"][0]["content"][1] == {"type": "image", "source": {
        "type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(b"fixture").decode()}}
    assert "temperature" not in sent and sent["max_tokens"] == claude.MIN_MAX_TOKENS
    assert sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]
    assert sent["output_config"] == {"effort": "low"}
    assert claude.usage == {"calls": 1, "input_tokens": 7, "output_tokens": 3}


def test_no_fallback_for_models_without_it():
    c, messages = _client('{"connected": false}', model="claude-haiku-5-5")
    c.structured(_image_message(), Answer)
    assert "fallbacks" not in messages.calls[0] and "betas" not in messages.calls[0]


def test_retries_once_on_schema_mismatch_then_fails():
    c, messages = _client('{"wrong": 1}', '{"connected": true}')
    assert c.structured(_image_message(), Answer).connected
    assert "previous answer was invalid" in messages.calls[1]["messages"][-1]["content"]
    c, _ = _client('{"wrong": 1}', '{"wrong": 2}')
    with pytest.raises(claude.ClaudeError, match="evidence format"):
        c.structured(_image_message(), Answer)


def test_refusal_and_truncation_are_errors():
    c, _ = _client(_response(None, "refusal"))
    with pytest.raises(claude.ClaudeError, match="declined"):
        c.structured(_image_message(), Answer)
    c, _ = _client(_response(None, "max_tokens"))
    with pytest.raises(claude.ClaudeError, match="output limit"):
        c.structured(_image_message(), Answer)


def test_api_errors_become_actionable_messages():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    low = anthropic.BadRequestError("Your credit balance is too low", response=httpx.Response(400, request=request), body=None)
    c, _ = _client(low)
    with pytest.raises(claude.ClaudeError, match="credit"):
        c.structured(_image_message(), Answer)
    bad_key = anthropic.AuthenticationError("invalid x-api-key", response=httpx.Response(401, request=request), body=None)
    c, _ = _client(bad_key)
    with pytest.raises(claude.ClaudeAccountError, match="did not accept"):
        c.structured(_image_message(), Answer)
    limited = anthropic.RateLimitError("slow down", response=httpx.Response(429, request=request), body=None)
    c, _ = _client(limited)
    with pytest.raises(claude.ClaudeError) as caught:  # passing: not an account problem
        c.structured(_image_message(), Answer)
    assert not isinstance(caught.value, claude.ClaudeAccountError)


def test_budget_exhaustion_blocks_before_calling():
    claude.reset_usage()
    claude.usage["calls"] = 999
    c, messages = _client('{"connected": true}')
    with pytest.raises(claude.ClaudeError, match="budget"):
        c.structured(_image_message(), Answer)
    assert messages.calls == []
    claude.reset_usage()


def test_rejects_non_inline_images_and_missing_key():
    c, _ = _client('{"connected": true}')
    with pytest.raises(claude.ClaudeError, match="inline"):
        c.structured([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.jpg"}}]}], Answer)
    with pytest.raises(claude.ClaudeError, match="not configured"):
        claude.ClaudeClient("EMPTY", "claude-opus-5-5")


def test_get_vlm_selects_claude(monkeypatch):
    monkeypatch.setattr(vlm, "settings", replace(vlm.settings, vlm_provider="anthropic", vlm_api_key="sk-test-fixture",
                                                 vlm_model="claude-opus-5-5"))
    provider = vlm.get_vlm()
    assert provider.name == "anthropic" and provider.available()
    assert isinstance(provider.client, claude.ClaudeClient) and provider.client.model == "claude-opus-5-5"
