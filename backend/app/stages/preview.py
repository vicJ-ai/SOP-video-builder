"""6. Side-by-side analysis preview in shared time, with the shared clock burned in.

Only the vision model reads it (it samples ~30 frames), so it is a fast FFmpeg pass over the 1080p
proxies, never a full Remotion render, and it is skipped when no vision model is configured.
"""

from __future__ import annotations

from ..config import settings
from ..pipeline import Ctx, Skip, Stage
from ..renderer import render_preview_ffmpeg

PREVIEW = "preview/analysis.mp4"


def _inputs(p):
    if p.mode == "demo":
        return {"demo": True}
    return {"sync": [p.sync.lag_ms, p.sync.shared_start_physical_ms, p.sync.shared_duration_ms],
            "sha": [p.physical.sha256, p.system.sha256], "vlm": settings.vlm_provider, "renderer": "ffmpeg-fast"}


def run(ctx: Ctx) -> str:
    from ..providers.vlm import get_vlm
    if ctx.project.mode == "demo":
        raise Skip("single recording — frames are read straight from it")
    if not get_vlm().available():
        raise Skip("no vision model configured — the analysis preview is not needed")
    p = ctx.project
    out = ctx.work / PREVIEW
    out.parent.mkdir(parents=True, exist_ok=True)
    render_preview_ffmpeg(ctx.proxy("physical"), ctx.proxy("system"), out,
                          phys_start_ms=p.sync.shared_start_physical_ms, sys_start_ms=p.sync.system_ms(0),
                          duration_ms=p.sync.shared_duration_ms, fps=settings.fps)
    ctx.provenance("preview", renderer="ffmpeg")
    return f"{p.sync.shared_duration_ms/1000:.1f}s side-by-side preview (ffmpeg)"


STAGE = Stage("preview", "Analysis preview", "2", _inputs, run)
