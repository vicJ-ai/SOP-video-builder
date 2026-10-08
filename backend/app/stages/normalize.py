"""2. Render-safe constant-frame-rate proxies. Same timestamps, same speed — never retimed."""

from __future__ import annotations

from ..config import settings
from ..media import make_proxy
from ..pipeline import Ctx, Stage


def _inputs(p):
    return {"sha": [p.physical.sha256, p.system.sha256], "fps": settings.fps}


def run(ctx: Ctx) -> str:
    notes = []
    for media in (ctx.project.physical, ctx.project.system):
        src = ctx.originals / media.filename
        name = f"proxy/{media.role}.mp4"
        out = ctx.work / name
        make_proxy(src, out, fps=settings.fps)
        media.proxy = name
        notes.append(f"{media.role}{' (VFR→CFR)' if media.vfr else ''}")
    return f"proxies at {settings.fps} fps: " + ", ".join(notes)


STAGE = Stage("normalize", "Normalize proxies", "2", _inputs, run)
