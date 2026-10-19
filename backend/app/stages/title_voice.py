"""Voice the procedure title + introduction, and size the Item title card to fit it.

Runs before the timeline so every later stage (narration scheduling, captions, render) sees the
final card length. The clip is cached by (text, voice); the tts stage reuses the same file.
"""

from __future__ import annotations

import math

from ..media import audio_duration_ms
from ..pipeline import Ctx, Skip, Stage
from ..providers.tts import get_tts
from ..store import stable_hash

MIN_TITLE_MS = 4000
LEAD_MS = 500  # card visible before the voice starts
TAIL_MS = 700  # pause after the voice before the footage begins


def _inputs(p):
    from .narrate import intro_line
    return {"line": intro_line(p) if p.template == "item-v1" else None, "voice": get_tts().voice_id()}


def clip_path(ctx: Ctx, text: str, tts) -> tuple[str, str]:
    h = stable_hash([text, tts.voice_id()])
    return f"tts/{h}.wav", h


def card_ms(voice_ms: int) -> int:
    """Title card length: at least 4 s, else lead + voice + tail, on the 30 fps frame grid."""
    frames = math.ceil(max(MIN_TITLE_MS, LEAD_MS + voice_ms + TAIL_MS) * 30 / 1000)
    return math.ceil(frames * 1000 / 30)


def run(ctx: Ctx) -> str:
    p = ctx.project
    if p.template != "item-v1":
        raise Skip("no Item title card in this design")
    from .narrate import intro_line
    text = intro_line(p)
    tts = get_tts()
    rel, _ = clip_path(ctx, text, tts)
    path = ctx.work / rel
    path.parent.mkdir(exist_ok=True)
    if not path.exists():
        tts.synthesize(text, path)
    voice_ms = audio_duration_ms(path)
    p.timeline.title_ms = card_ms(voice_ms)
    return f"title + introduction voiced ({voice_ms/1000:.1f}s); title card {p.timeline.title_ms/1000:.1f}s"


STAGE = Stage("title_voice", "Voice title & introduction", "1", _inputs, run)
