"""3-4. Synchronize the two recordings and verify the offset at start / middle / end.

Estimators (each recorded, none trusted blindly):
  * SkellySync (AGPL-3.0, out-of-process) — audio cross-correlation or brightness
  * built-in GCC-PHAT and onset-envelope correlation on the original audio
  * built-in brightness-change correlation (when a recording has no audio)
The chosen lag is the candidate with the smallest error across the three independent
verification windows. Constant offset is fixed by the lag; drift (window errors that grow over
time) cannot be fixed by trimming and is flagged for review. A reviewer-set lag always wins.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .. import syncalgo
from ..config import settings
from ..media import audio_samples, gray_frames
from ..models import OutsideFootage, SyncCheck, SyncEstimate, SyncResult
from ..pipeline import Ctx, Stage

log = logging.getLogger(__name__)

SR = 8000
CHECK_WINDOW_MS = 20_000
SEARCH_MARGIN_MS = 1_500
CHECK_TOLERANCE_MS = 80
DRIFT_LIMIT_MS = 80
AGREE_MS = 100


def _inputs(p):
    return {
        "sha": [p.physical.sha256, p.system.sha256],
        "override": p.sync_override_ms,
        "skellysync": Path(settings.skellysync_python).exists(),
    }


# --------------------------------------------------------------------------- estimators


def _skellysync(ctx: Ctx, mode: str) -> SyncEstimate | None:
    py = Path(settings.skellysync_python)
    if not py.exists():
        return None
    with tempfile.TemporaryDirectory(prefix="skellysync-", dir=ctx.work) as tmp:
        raw = Path(tmp) / "raw_videos"
        raw.mkdir()
        names = {}
        for role, src in (("physical", ctx.proxy("physical")), ("system", ctx.proxy("system"))):
            dst = raw / f"{role}.mp4"
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
            names[role] = dst.name
        synced = Path(tmp) / "synchronized_videos"
        fn = "synchronize_videos_from_audio" if mode == "audio" else "synchronize_videos_from_brightness"
        script = (
            "import sys\nfrom pathlib import Path\n"
            f"from skelly_synchronize.skelly_synchronize import {fn}\n"
            f"{fn}(raw_video_folder_path=Path(sys.argv[1]), synchronized_video_folder_path=Path(sys.argv[2]),"
            " video_handler='ffmpeg', create_debug_plots_bool=False)\n"
        )
        env = {**os.environ, "PATH": f"{Path(settings.ffmpeg).parent}{os.pathsep}{os.environ.get('PATH', '')}"}
        proc = subprocess.run([str(py), "-c", script, str(raw), str(synced)], capture_output=True, env=env,
                              timeout=3600)
        toml_path = synced / "synchronization_debug.toml"
        if proc.returncode != 0 or not toml_path.exists():
            log.warning("skellysync failed: %s", proc.stderr.decode(errors="replace")[-800:])
            return SyncEstimate(method=f"skellysync-{mode}", lag_ms=0, score=0.0, detail="failed")
        try:
            import tomllib
        except ImportError:  # pragma: no cover
            return None
        lags = tomllib.loads(toml_path.read_text())["Lag_dictionary"]
        trim_p = float(lags[names["physical"]])
        trim_s = float(lags[names["system"]])
        # Each value is how much of that video precedes the first shared instant.
        lag_ms = int(round((trim_p - trim_s) * 1000))
        (ctx.work / "sync").mkdir(exist_ok=True)
        shutil.copy2(toml_path, ctx.work / "sync" / f"skellysync_{mode}_debug.toml")
        return SyncEstimate(method=f"skellysync-{mode}", lag_ms=lag_ms, score=0.6,
                            detail=f"trim physical {trim_p:.3f}s, system {trim_s:.3f}s")



# --------------------------------------------------------------------------- verification


def _local_lag(ctx: Ctx, lag_ms: int, shared_start_phys: int, centre_shared: int, half: int,
               audio: bool) -> tuple[int | None, float]:
    phys_start = shared_start_phys + centre_shared - half
    sys_start = max(0, phys_start - lag_ms - SEARCH_MARGIN_MS)
    dur = 2 * half
    search_s = 2 * SEARCH_MARGIN_MS / 1000 + 0.1
    if audio:
        a = audio_samples(ctx.proxy("physical"), sr=SR, start_ms=phys_start, dur_ms=dur)
        b = audio_samples(ctx.proxy("system"), sr=SR, start_ms=sys_start, dur_ms=dur + 2 * SEARCH_MARGIN_MS)
        if np.sqrt(np.mean(a ** 2)) < 1e-4 or np.sqrt(np.mean(b ** 2)) < 1e-4:
            return None, 0.0
        ests = syncalgo.audio_lag(a, b, SR, max_lag_s=search_s)
        lag_s, score = max(ests.values(), key=lambda v: v[1])
    else:
        fps = 30.0
        a = gray_frames(ctx.proxy("physical"), fps=fps, width=32, height=18, start_ms=phys_start, dur_ms=dur)
        b = gray_frames(ctx.proxy("system"), fps=fps, width=32, height=18, start_ms=sys_start,
                        dur_ms=dur + 2 * SEARCH_MARGIN_MS)
        lag_s, score = syncalgo.brightness_lag(a, b, fps, max_lag_s=search_s)
    # a(t) ≈ b(t - lag_s) in window-local clocks, so the instant at physical phys_start+t is at
    # system sys_start+t-lag_s; physical = system + local_lag.
    return int(round(phys_start - sys_start + lag_s * 1000)), score


def _verify(ctx: Ctx, lag_ms: int, audio: bool) -> tuple[list[SyncCheck], int, int, int]:
    p = ctx.project
    shared_start = max(0, lag_ms)
    shared_end = min(p.physical.duration_ms, lag_ms + p.system.duration_ms)
    duration = shared_end - shared_start
    checks: list[SyncCheck] = []
    if duration <= 0:
        return checks, shared_start, duration, 0
    half = min(CHECK_WINDOW_MS, duration // 3) // 2
    centres = {"start": half, "middle": duration // 2, "end": duration - half}
    for label, centre in centres.items():
        try:
            local, score = _local_lag(ctx, lag_ms, shared_start, centre, half, audio)
        except Exception as exc:  # noqa: BLE001
            log.warning("sync check %s failed: %s", label, exc)
            local, score = None, 0.0
        err = None if local is None else local - lag_ms
        checks.append(SyncCheck(label=label, shared_ms=centre, local_lag_ms=local, error_ms=err, score=round(score, 3),
                                ok=err is not None and abs(err) <= CHECK_TOLERANCE_MS and score >= 0.15))
    locals_ = [c.local_lag_ms for c in checks if c.local_lag_ms is not None and c.score >= 0.15]
    drift = (max(locals_) - min(locals_)) if len(locals_) >= 2 else 0
    return checks, shared_start, duration, drift


def _outside(ctx: Ctx, shared_start: int, duration: int, lag_ms: int) -> list[OutsideFootage]:
    """Flag activity outside the shared interval instead of silently dropping it."""
    p = ctx.project
    flagged: list[OutsideFootage] = []
    ranges = {
        "physical": [(0, shared_start), (shared_start + duration, p.physical.duration_ms)],
        "system": [(0, shared_start - lag_ms), (shared_start + duration - lag_ms, p.system.duration_ms)],
    }
    for role, spans in ranges.items():
        for start, end in spans:
            if end - start < 1500:
                continue
            frames = gray_frames(ctx.proxy(role), fps=4, start_ms=start, dur_ms=end - start)
            active = syncalgo.activity_mask(frames, threshold=4.0 if role == "system" else 6.0)
            share = float(active.mean()) if len(active) else 0.0
            if share >= 0.1:
                what = "screen changes" if role == "system" else "physical motion"
                flagged.append(OutsideFootage(role=role, source_start_ms=start, source_end_ms=end,
                                              reason=f"{what} in {share:.0%} of {(end - start)/1000:.1f}s outside the shared interval"))
    return flagged


# --------------------------------------------------------------------------- stage


def run(ctx: Ctx) -> str:
    p = ctx.project
    audio = p.physical.has_audio and p.system.has_audio
    max_lag_s = max(p.physical.duration_ms, p.system.duration_ms) / 1000
    estimates: list[SyncEstimate] = []

    if audio:
        phys = audio_samples(ctx.proxy("physical"), sr=SR)
        sys_ = audio_samples(ctx.proxy("system"), sr=SR)
        for method, (lag_s, score) in syncalgo.audio_lag(phys, sys_, SR, max_lag_s=max_lag_s).items():
            estimates.append(SyncEstimate(method=method, lag_ms=int(round(lag_s * 1000)), score=round(score, 3)))
    fps = 15.0
    pf = gray_frames(ctx.proxy("physical"), fps=fps, width=32, height=18)
    sf = gray_frames(ctx.proxy("system"), fps=fps, width=32, height=18)
    lag_s, score = syncalgo.brightness_lag(pf, sf, fps, max_lag_s=max_lag_s)
    estimates.append(SyncEstimate(method="brightness-xcorr", lag_ms=int(round(lag_s * 1000)),
                                  score=round(score * (0.5 if audio else 1.0), 3)))

    if est := _skellysync(ctx, "audio" if audio else "brightness"):
        estimates.append(est)

    reasons: list[str] = []
    if p.sync_override_ms is not None:
        estimates.append(SyncEstimate(method="manual", lag_ms=p.sync_override_ms, score=1.0, detail="set by reviewer"))
        chosen = estimates[-1]
        checks, shared_start, duration, drift = _verify(ctx, chosen.lag_ms, audio)
    else:
        candidates = [e for e in estimates if e.score >= 0.05 and e.detail != "failed"]
        if not candidates:
            candidates = estimates
        # dedupe near-identical lags, then pick the one that best survives independent windows
        tried: dict[int, tuple] = {}
        for est in sorted(candidates, key=lambda e: -e.score):
            if any(abs(est.lag_ms - k) <= 20 for k in tried):
                continue
            checks, shared_start, duration, drift = _verify(ctx, est.lag_ms, audio)
            errs = [abs(c.error_ms) for c in checks if c.error_ms is not None and c.score >= 0.15]
            cost = (np.mean(errs) if errs else 10_000) - 50 * est.score
            tried[est.lag_ms] = (cost, est, checks, shared_start, duration, drift)
        _, chosen, checks, shared_start, duration, drift = min(tried.values(), key=lambda t: t[0])

        # second-opinion agreement
        strong = [e for e in estimates if e.score >= 0.3 and e.detail != "failed"]
        disagree = [e for e in strong if abs(e.lag_ms - chosen.lag_ms) > AGREE_MS]
        if disagree:
            reasons.append("estimators disagree: " + ", ".join(f"{e.method} {e.lag_ms}ms" for e in disagree))

    if duration <= 0:
        raise ValueError(f"recordings do not overlap at lag {chosen.lag_ms} ms — set the offset manually")
    failed = [c.label for c in checks if not c.ok]
    if failed:
        reasons.append(f"verification window(s) not confirmed: {', '.join(failed)}")
    if drift > DRIFT_LIMIT_MS:
        reasons.append(f"drift {drift} ms between start/middle/end — trimming cannot fix drift")
    if not audio:
        reasons.append("no audio in one recording — brightness sync only, confirm visually")

    outside = _outside(ctx, shared_start, duration, chosen.lag_ms)
    p.sync = SyncResult(
        lag_ms=chosen.lag_ms, method=chosen.method, estimates=estimates, checks=checks, drift_ms=drift,
        shared_start_physical_ms=shared_start, shared_duration_ms=duration, outside=outside,
        manual=chosen.method == "manual", needs_review=bool(reasons) and chosen.method != "manual",
        review_reasons=reasons,
    )
    (ctx.work / "sync").mkdir(exist_ok=True)
    (ctx.work / "sync" / "sync.json").write_text(p.sync.model_dump_json(indent=2))
    _write_waveforms(ctx)
    ctx.provenance("sync", estimators=[e.method for e in estimates], chosen=chosen.method)
    flag = " — needs review" if p.sync.needs_review else ""
    return f"lag {chosen.lag_ms} ms via {chosen.method}; shared {duration/1000:.1f}s; drift {drift} ms{flag}"


def _write_waveforms(ctx: Ctx) -> None:
    """Downsampled peak envelopes (100 Hz) for the review UI's paired waveform view."""
    out = {}
    for role in ("physical", "system"):
        media = getattr(ctx.project, role)
        if not media.has_audio:
            out[role] = []
            continue
        s = audio_samples(ctx.proxy(role), sr=SR)
        hop = SR // 100
        n = len(s) // hop
        env = np.abs(s[: n * hop]).reshape(n, hop).max(axis=1) if n else np.zeros(0)
        out[role] = np.round(env / (env.max() or 1), 3).tolist()
    (ctx.work / "sync" / "waveforms.json").write_text(json.dumps({"rate": 100, **out}))


STAGE = Stage("sync", "Synchronize & verify", "1", _inputs, run)
