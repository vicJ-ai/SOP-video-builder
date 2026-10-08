"""6. Clean analysis preview (Remocn/Remotion 'Preview' composition) in shared time."""

from __future__ import annotations

from ..config import settings
from ..pipeline import Ctx, Stage
from ..renderer import frames, remotion_available, render_preview_ffmpeg, render_remotion

PREVIEW = "preview/analysis.mp4"


def _inputs(p):
    return {"sync": [p.sync.lag_ms, p.sync.shared_start_physical_ms, p.sync.shared_duration_ms],
            "sha": [p.physical.sha256, p.system.sha256], "renderer": settings.renderer, "title": p.title}


def run(ctx: Ctx) -> str:
    p = ctx.project
    fps = settings.fps
    out = ctx.work / PREVIEW
    phys_start = p.sync.shared_start_physical_ms
    sys_start = p.sync.system_ms(0)
    if settings.renderer == "remotion" and remotion_available():
        props = {
            "fps": fps, "width": 1920, "height": 1080, "durationInFrames": frames(p.sync.shared_duration_ms, fps),
            "title": p.title,
            "physical": {"src": p.physical.proxy, "width": p.physical.width, "height": p.physical.height, "label": "floor camera"},
            "system": {"src": p.system.proxy, "width": p.system.width, "height": p.system.height, "label": "WISE"},
            "physicalStartFrame": frames(phys_start, fps), "systemStartFrame": frames(sys_start, fps),
        }
        render_remotion("Preview", props, out, public_dir=ctx.work, log_path=ctx.work / "preview" / "remotion.log")
        how = "remotion"
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        render_preview_ffmpeg(ctx.proxy("physical"), ctx.proxy("system"), out, phys_start_ms=phys_start,
                              sys_start_ms=sys_start, duration_ms=p.sync.shared_duration_ms, fps=fps)
        how = "ffmpeg"
    ctx.provenance("preview", renderer=how)
    return f"{p.sync.shared_duration_ms/1000:.1f}s side-by-side preview ({how})"


STAGE = Stage("preview", "Analysis preview", "1", _inputs, run)
