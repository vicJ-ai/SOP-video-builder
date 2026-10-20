"""17-19. Draft preview and final render (Remocn/Remotion 'Final' composition, or FFmpeg fallback).

Narrated versions mute the original recording audio.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from ..config import settings
from ..pipeline import Ctx, Stage
from ..renderer import frames, remotion_available, render_final_ffmpeg, render_remotion
from .captions import heading_for

log = logging.getLogger(__name__)
DRAFT_PATH = "draft/draft.mp4"
FINAL_NAME = "final.mp4"


def build_props(ctx: Ctx, *, draft: bool) -> dict:
    p = ctx.project
    fps = settings.fps
    sync = p.sync
    segments = []
    for s in p.timeline.segments:
        segments.append({
            "kind": s.kind, "fromFrame": s.from_frame if s.from_frame is not None else frames(s.out_start_ms, fps),
            "durationInFrames": s.duration_frames if s.duration_frames is not None else max(1, frames(s.duration_ms, fps)),
            "availableRoles": s.available_roles or ["physical", "system"],
            "physicalStartFrame": frames(sync.physical_ms(s.shared_start_ms), fps) if s.kind in ("source", "hold") else 0,
            "systemStartFrame": frames(sync.system_ms(s.shared_start_ms), fps) if s.kind in ("source", "hold") else 0,
        })
    cues = [{"id": c.id, "src": c.audio, "fromFrame": c.from_frame if c.from_frame is not None else frames(c.out_start_ms, fps),
             "durationInFrames": c.duration_frames or max(1, frames(c.out_end_ms - c.out_start_ms, fps) + 1),
             "text": c.spoken_text or c.text}
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
        "title": _clip(p.sop.title or p.title, 110), "draft": draft,
        "subtitle": _clip(p.sop.purpose, 180) if p.sop.purpose else "Standard Operating Procedure",
        "physical": {"src": p.physical.proxy, "width": p.physical.width, "height": p.physical.height, "label": "Floor"},
        "system": {"src": p.system.proxy, "width": p.system.width, "height": p.system.height, "label": "WISE"},
        # Title card "demonstrated workflow": the SOP's first steps (token-free; no model involved).
        "workflow": [_clip(heading_for(s.title or s.text, s.text), 70) for s in p.sop_steps][:4],
        "template": p.template, "demo": False,
        "segments": segments, "cues": cues,
        "captions": [{"startMs": c.start_ms, "endMs": c.end_ms, "fromFrame": c.from_frame, "endFrame": c.end_frame,
                      "text": c.text.replace("\n", " ")} for c in p.captions],
        "layout": [{"fromFrame": frames(k.out_start_ms, fps), "toFrame": frames(k.out_end_ms, fps), "focus": k.focus,
                    "heading": k.heading, "stepIndex": k.step_index} for k in p.layout],
        "steps": [{"id": s.id, "number": s.number, "text": s.text, "status": status.get(s.id, "missing")} for s in p.sop_steps],
        "flags": sorted(flags, key=lambda f: f["fromFrame"])[:60],
    }


def _clip(text: str, limit: int) -> str:
    """The Item title/heading fitters cancel a render whose text cannot fit; keep wording within bounds."""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rsplit(" ", 1)[0] + "…"


def _reporter(ctx: Ctx, stage: str):
    """Show live render progress as the running stage's message (the web page polls it)."""
    def report(phase: str, done: int, total: int, eta: str) -> None:
        pct = round(100 * done / max(1, total))
        verb = "Rendering video" if phase == "rendering" else "Encoding video"
        ctx.project.stages[stage].message = f"{verb}: {done:,} / {total:,} frames ({pct}%)" + (f" · about {eta} left" if eta else "")
        try:
            ctx.store.save(ctx.project)
        except OSError as exc:  # a progress update must never fail the render itself
            log.warning("progress update not saved: %s", exc)
    return report


def _render(ctx: Ctx, *, draft: bool):
    out = ctx.work / DRAFT_PATH if draft else ctx.out / FINAL_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    props = build_props(ctx, draft=draft)
    item = ctx.project.template == "item-v1"
    from ..brand import prepare
    prepare(ctx.work)  # hash-checked logo, Satoshi fonts and bookends -> work/brand, work/fonts (every design uses them)
    if settings.renderer == "remotion" and remotion_available():
        # Serve only the referenced proxies, voice clips and brand assets, not the whole work tree.
        with tempfile.TemporaryDirectory(prefix="render-public-", dir=ctx.work) as temp:
            public = Path(temp)
            paths = {m.proxy for m in (ctx.project.physical, ctx.project.system) if m} | {c["src"] for c in props["cues"]}
            paths |= {str(f.relative_to(ctx.work)) for folder in ("brand", "fonts")
                      for f in (ctx.work / folder).glob("*") if f.is_file()}
            for rel in paths:
                destination = public / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.link(ctx.work / rel, destination)  # same volume: the temp dir lives inside work/
            render_remotion("Final", props, out, public_dir=public, scale=0.5 if draft else 1.0,
                            log_path=out.with_suffix(".log"), on_progress=_reporter(ctx, "render"))
        how = "remotion"
    elif item:
        raise RuntimeError("The Item video design needs the Remotion renderer; the FFmpeg fallback cannot draw it")
    else:
        render_final_ffmpeg(props, ctx.work, out, captions_srt=ctx.work / "captions" / "captions.srt")
        how = "ffmpeg"
    return out, how, props


def _inputs(p):
    return {"cues": [[c.id, c.audio_hash, c.out_start_ms, c.out_end_ms, c.dropped] for c in p.cues],
            "captions": [c.model_dump() for c in p.captions], "layout": [k.model_dump() for k in p.layout],
            "segments": [s.model_dump() for s in p.timeline.segments], "sync": p.sync.lag_ms,
            "coverage": p.coverage.model_dump(), "title": [p.title, p.sop.title], "renderer": settings.renderer,
            "template": p.template, "steps": [[s.id, s.title, s.text] for s in p.sop_steps], "purpose": p.sop.purpose,
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
FINAL = Stage("render", "Final render", "2", _final_inputs, run_final)
