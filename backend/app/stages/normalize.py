"""2. Render-safe constant-frame-rate proxies. Same timestamps, same speed — never retimed."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ..config import settings
from ..media import make_proxy
from ..pipeline import Ctx, Stage


def _inputs(p):
    if p.mode == "demo":
        return {"demo": [[m.role, m.sha256] for m in p.sources()], "fps": settings.fps}
    return {"sha": [p.physical.sha256, p.system.sha256], "fps": settings.fps}


def run(ctx: Ctx) -> str:
    notes = []
    medias = ctx.project.sources()
    # Both proxies at once: the 4K floor recording dominates, the phone recording finishes alongside it.
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(make_proxy, ctx.originals / m.filename, ctx.work / f"proxy/{m.role}.mp4", fps=settings.fps)
                for m in medias]
        for job in jobs:
            job.result()
    for media in medias:
        media.proxy = f"proxy/{media.role}.mp4"
        notes.append(f"{media.role}{' (VFR→CFR)' if media.vfr else ''}")
    return f"proxies at {settings.fps} fps: " + ", ".join(notes)


STAGE = Stage("normalize", "Normalize proxies", "3", _inputs, run)
