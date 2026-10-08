"""16 + 18. Captions (SRT/VTT/burned-in) from the measured schedule, and event-driven layout keys."""

from __future__ import annotations

import re

from ..captions_fmt import build_captions, to_srt, to_vtt
from ..models import LayoutKey
from ..pipeline import Ctx, Stage

FOCUS = {"system": "system", "physical": "physical", "both": "split"}
LAYOUT_LEAD_MS = 300


def _inputs(p):
    return {"cues": [[c.id, c.event_id, c.spoken_text or c.text, c.out_start_ms, c.out_end_ms, c.dropped] for c in p.cues],
            "events": [[e.id, e.view, e.sop_step_id, e.action] for e in p.live_events()],
            "steps": [[s.id, s.title, s.text] for s in p.sop_steps], "segments": [s.model_dump() for s in p.timeline.segments]}


def heading_for(step_text: str, fallback: str) -> str:
    text = re.split(r"(?<=[.!?])\s", step_text or fallback)[0].rstrip(".")
    return text if len(text) <= 56 else text[:53].rsplit(" ", 1)[0] + "…"


def build_layout(p) -> list[LayoutKey]:
    order = {s.id: i + 1 for i, s in enumerate(p.sop_steps)}
    keys: list[LayoutKey] = []
    events = {e.id: e for e in p.live_events()}
    intro_end = next((s.out_end_ms for s in p.timeline.segments if s.kind == "intro"), 0)
    for cue in sorted((c for c in p.cues if not c.dropped and c.anchor_out_ms is not None), key=lambda c: c.anchor_out_ms):
        e = events.get(cue.event_id)
        if not e:
            continue
        step = p.step(e.sop_step_id)
        start = max(intro_end, min(cue.out_start_ms, cue.anchor_out_ms) - LAYOUT_LEAD_MS)
        key = LayoutKey(out_start_ms=start, out_end_ms=p.timeline.duration_ms, focus=FOCUS[e.view],
                        heading=heading_for((step.title or step.text) if step else "", e.action), step_id=e.sop_step_id,
                        step_index=order.get(e.sop_step_id) or (keys[-1].step_index if keys else None))
        if keys and keys[-1].focus == key.focus and keys[-1].heading == key.heading:
            continue
        if keys:
            keys[-1].out_end_ms = start
        keys.append(key)
    return [k for k in keys if k.out_end_ms > k.out_start_ms]


def run(ctx: Ctx) -> str:
    p = ctx.project
    p.captions = build_captions(p.cues)
    p.layout = build_layout(p)
    out = ctx.work / "captions"
    out.mkdir(exist_ok=True)
    (out / "captions.srt").write_text(to_srt(p.captions), encoding="utf-8")
    (out / "captions.vtt").write_text(to_vtt(p.captions), encoding="utf-8")
    return f"{len(p.captions)} caption pages, {len(p.layout)} layout changes"


STAGE = Stage("captions", "Captions & layout", "1", _inputs, run)
