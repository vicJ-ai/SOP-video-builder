"""Qwen3-VL video understanding behind an OpenAI-compatible endpoint (or a mock)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..config import settings
from .openai_compat import ChatClient, image_part

SYSTEM_PROMPT = """You analyse warehouse standard-operating-procedure footage for training videos.
Rules:
- Report ONLY actions and screen states you can actually see in the frames. Never infer an action because a
  procedure would normally include it. If something is unclear, say so and lower confidence.
- "system" = something happening on the WISE warehouse-system screen (tap, typing, scan result, dialog,
  confirmation). "physical" = handling in the warehouse (pick, lift, scan with handheld, place, label, travel).
  "both" = one action visibly spans both views.
- kind "result" means a visible outcome (success message, status change, label printed), not an action.
- visible_text: copy control names / messages / identifiers EXACTLY as written on screen, only when readable.
- Timestamps are seconds on the shared clock shown in each frame label. Keep start <= end.
Return JSON only."""


class VlmEvent(BaseModel):
    start_s: float
    end_s: float
    view: Literal["system", "physical", "both"]
    action: str = Field(min_length=3, max_length=300)
    kind: Literal["action", "result"] = "action"
    visible_text: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1, default=0.5)


class VlmEvents(BaseModel):
    events: list[VlmEvent] = Field(default_factory=list)


class VisibleText(BaseModel):
    lines: list[str] = Field(default_factory=list)


class Frame(BaseModel):
    t_ms: int
    label: str  # e.g. "COMBINED", "SYSTEM", "PHYSICAL"
    jpeg: bytes


class Vlm:
    name = "mock"

    def available(self) -> bool:
        return False

    def describe_window(self, frames: list[Frame], *, instruction: str) -> VlmEvents:  # pragma: no cover
        raise NotImplementedError

    def read_text(self, jpeg: bytes) -> list[str]:  # pragma: no cover
        raise NotImplementedError


class OpenAIVlm(Vlm):
    name = "openai"

    def __init__(self, client=None):
        self.client = client or ChatClient(settings.vlm_base_url, settings.vlm_api_key, settings.vlm_model)

    def available(self) -> bool:
        return True

    def describe_window(self, frames: list[Frame], *, instruction: str) -> VlmEvents:
        content: list[dict] = [{"type": "text", "text": instruction}]
        for f in frames:
            content.append({"type": "text", "text": f"[{f.label} t={f.t_ms / 1000:.2f}s]"})
            content.append(image_part(f.jpeg))
        content.append({"type": "text", "text": 'Answer as {"events": [...]} using the schema fields '
                                                "start_s, end_s, view, action, kind, visible_text, confidence."})
        return self.client.structured(
            [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}], VlmEvents)

    def read_text(self, jpeg: bytes) -> list[str]:
        out = self.client.structured([
            {"role": "system", "content": "You are an OCR engine. Transcribe every readable text line exactly."},
            {"role": "user", "content": [image_part(jpeg), {"type": "text", "text": 'Return {"lines": [...]}.'}]},
        ], VisibleText)
        return out.lines


def get_vlm() -> Vlm:
    if settings.vlm_provider == "anthropic":
        from .anthropic_client import ClaudeClient
        provider = OpenAIVlm(ClaudeClient(settings.vlm_api_key, settings.vlm_model))
        provider.name = "anthropic"
        return provider
    return OpenAIVlm() if settings.vlm_provider == "openai" else Vlm()
