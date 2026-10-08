"""Optional text LLM (any OpenAI-compatible endpoint). Every stage has a deterministic path without it."""

from __future__ import annotations

from ..config import settings
from .openai_compat import ChatClient


def get_llm() -> ChatClient | None:
    if settings.llm_provider != "openai":
        return None
    return ChatClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
