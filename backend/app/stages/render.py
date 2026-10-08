"""17-19. Draft preview and final render (Remocn/Remotion 'Final' composition, or FFmpeg fallback).

Narrated versions mute the original recording audio.
"""

from __future__ import annotations

from ..config import settings
from ..pipeline import Ctx, Stage
from ..renderer import frames, remotion_available, render_final_ffmpeg, render_remotion

DRAFT_PATH = "draft/draft.mp4"
FINAL_NAME = "final.mp4"


def build_props(ctx: Ctx, *, draft: bool) -> dict:
    p = ctx.project
    fps = settings.fps
    sync = p.sync
    segments = []
    for s in p.timeline.segments:
        segments.append({
            "kind": s.kind, "fromFrame": frames(s.out_start_ms, fps), "durationInFrames": max(1, frames(s.duration_ms, fps)),
            "physicalStartFrame": frames(sync.physical_ms(s.shared_start_ms), fps) if s.kind == "source" else 0,
            "systemStartFrame": frames(sync.system_ms(s.shared_start_ms), fps) if s.kind == "source" else 0,
        })
    cues = [{"id": c.id, "src": c.audio, "fromFrame": frames(c.out_start_ms, fps),
             "durationInFrames": max(1, frames(c.out_end_ms - c.out_start_ms, fps) + 1), "text": c.spoken_text or c.text}
            for c in p.cues if not c.dropped and c.out_start_ms is not None and c.audio]
    status = {c.step_id: c.effective for c in p.coverage.steps}
    flags = []
    if draft:
        for d in p.coverage.discrepancies:
            ev = p.event(d.event_id) if d.event_id else None
            at = p.timeline.to_output(ev.start_ms) if ev else None
            if at is None and d.step_id:
                cue = next((c for c in p.cues if c.sop_step_id == d.step_id and c.out_start_ms is not None), None)
                at = cue.out_start_ms if cue else None
            if at is not None:
                flags.append({"fromFrame": frames(at, fps), "toFrame": frames(at + 3500, fps),
                              "title": f"SOP flag · {d.kind.replace('-', ' ')}", "description": d.message})
        for e in p.live_events():
            at = p.timeline.to_output(e.start_ms)
            if e.review_required and not e.approved and at is not None:
                flags.append({"fromFrame": frames(at, fps), "toFrame": frames(at + 2500, fps),
                              "title": "Needs review", "description": (e.review_reasons or [e.action])[0]})
    return {
        "fps": fps, "width": 1920, "height": 1080, "durationInFrames": frames(p.timeline.duration_ms, fps),
        "title": p.sop.title or p.title, "subtitle": "Standard Operating Procedure", "draft": draft,
        "physical": {"src": p.physical.proxy, "width": p.physical.width, "height": p.physical.height, "label": "Floor"},
        "system": {"src": p.system.proxy, "width": p.system.width, "height": p.system.height, "label": "WISE"},
        "segments": segments, "cues": cues,
        "captions": [{"startMs": c.start_ms, "endMs": c.end_ms, "text": c.text.replace("\n", " ")} for c in p.captions],
        "layout": [{"fromFrame": frames(k.out_start_ms, fps), "toFrame": frames(k.out_end_ms, fps), "focus": k.focus,
                    "heading": k.heading, "stepIndex": k.step_index} for k in p.layout],
        "steps": [{"id": s.id, "number": s.number, "text": s.text, "status": status.get(s.id, "missing")} for s in p.sop_steps],
        "flags": sorted(flags, key=lambda f: f["fromFrame"])[:60],
    }


def _render(ctx: Ctx, *, draft: bool):
    out = ctx.work / DRAFT_PATH if draft else ctx.out / FINAL_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    props = build_props(ctx, draft=draft)
    if settings.renderer == "remotion" and remotion_available():
        render_remotion("Final", props, out, public_dir=ctx.work, scale=0.5 if draft else 1.0,
                        log_path=out.with_suffix(".log"))
        how = "remotion"
    else:
        render_final_ffmpeg(props, ctx.work, out, captions_srt=ctx.work / "captions" / "captions.srt")
        how = "ffmpeg"
    return out, how, props


def _inputs(p):
    return {"cues": [[c.id, c.audio_hash, c.out_start_ms, c.out_end_ms, c.dropped] for c in p.cues],
            "captions": [c.model_dump() for c in p.captions], "layout": [k.model_dump() for k in p.layout],
            "segments": [s.model_dump() for s in p.timeline.segments], "sync": p.sync.lag_ms,
            "coverage": p.coverage.model_dump(), "title": [p.title, p.sop.title], "renderer": settings.renderer,
            "review": [[e.id, e.review_required, e.approved] for e in p.events]}


def run_draft(ctx: Ctx) -> str:
    out, how, props = _render(ctx, draft=True)
    return f"draft {props['durationInFrames'] / settings.fps:.1f}s at half resolution ({how}); {len(props['flags'])} on-screen flags"


def _final_inputs(p):
    base = _inputs(p)
    base.pop("review")
    base["coverage"] = None
    return base


def run_final(ctx: Ctx) -> str:
    out, how, props = _render(ctx, draft=False)
    ctx.provenance("render", renderer=how, composition="Final", fps=settings.fps, size=[1920, 1080])
    return f"{out.name}: {props['durationInFrames'] / settings.fps:.1f}s 1920x1080 ({how})"


DRAFT = Stage("draft", "Draft preview", "1", _inputs, run_draft)
FINAL = Stage("render", "Final render", "1", _final_inputs, run_final)
