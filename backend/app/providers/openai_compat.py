"""Minimal OpenAI-compatible chat client (vLLM, SGLang, DashScope compatible mode, ...)."""

from __future__ import annotations

import base64
import json
import re
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


def image_part(jpeg: bytes) -> dict:
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}


def _extract_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=0)
    return json.loads(text[start:])


class ChatClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 300):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.http = httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {api_key}"})

    def chat(self, messages: list[dict], *, schema: type[BaseModel] | None = None, temperature: float = 0.0,
             max_tokens: int = 4096) -> str:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature,
                                "max_tokens": max_tokens}
        if schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": schema.__name__, "schema": schema.model_json_schema(), "strict": False}}
        resp = self.http.post(f"{self.base_url}/chat/completions", json=body)
        if resp.status_code == 400 and schema is not None:
            # endpoint without structured-output support: fall back to prompted JSON
            body.pop("response_format")
            resp = self.http.post(f"{self.base_url}/chat/completions", json=body)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"] or ""

    def structured(self, messages: list[dict], schema: type[T], *, retries: int = 1, **kw) -> T:
        """Schema-validated output; on failure, show the model its validation error and retry."""
        last_err = ""
        for _ in range(retries + 1):
            msgs = messages if not last_err else messages + [
                {"role": "user", "content": f"Your previous answer was invalid: {last_err[:800]}. "
                                            "Reply again with only valid JSON matching the schema."}]
            text = self.chat(msgs, schema=schema, **kw)
            try:
                return schema.model_validate(_extract_json(text))
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                last_err = str(exc)
        raise ValueError(f"model output failed validation: {last_err}")
