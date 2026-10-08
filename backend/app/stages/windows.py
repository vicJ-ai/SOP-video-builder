"""8. Candidate action windows from cheap signals.

Warehouse SOP footage is mostly long continuous shots, so scene cuts alone are not enough:
combine WISE screen changes, physical motion, audio peaks (scanner beeps, taps) and PySceneDetect.
"""

from __future__ import annotations

import numpy as np

from .. import syncalgo
from ..media import audio_samples, gray_frames
from ..models import CandidateWindow
from ..pipeline import Ctx, Stage

FPS = 4.0
PAD_MS = 500
MAX_WINDOW_MS = 8000


def _inputs(p):
    return {"sync": [p.sync.lag_ms, p.sync.shared_start_physical_ms, p.sync.shared_duration_ms],
            "sha": [p.physical.sha256, p.system.sha256]}


def _runs(mask: np.ndarray, rate: float) -> list[tuple[int, int]]:
    out, start = [], None
    for i, on in enumerate(list(mask) + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            out.append((int(start * 1000 / rate), int(i * 1000 / rate)))
            start = None
    return out


def _scenedetect(ctx: Ctx, role: str, start_ms: int, dur_ms: int) -> list[int]:
    try:
        from scenedetect import ContentDetector, detect
    except ImportError:
        return []
    try:
        scenes = detect(str(ctx.proxy(role)), ContentDetector(threshold=27.0))
    except Exception:  # noqa: BLE001
        return []
    cuts = [int(s[0].get_seconds() * 1000) - start_ms for s in scenes[1:]]
    return [c for c in cuts if 0 <= c < dur_ms]


def run(ctx: Ctx) -> str:
    p = ctx.project
    dur = p.sync.shared_duration_ms
    spans: list[tuple[int, int, str, str, float]] = []  # start, end, signal, view, strength

    sys_frames = gray_frames(ctx.proxy("system"), fps=FPS, start_ms=p.sync.system_ms(0), dur_ms=dur)
    phys_frames = gray_frames(ctx.proxy("physical"), fps=FPS, start_ms=p.sync.physical_ms(0), dur_ms=dur)
    for s, e in _runs(syncalgo.activity_mask(sys_frames, threshold=2.5), FPS):
        spans.append((s, e, "screen-change", "system", 1.0))
    for s, e in _runs(syncalgo.activity_mask(phys_frames, threshold=8.0), FPS):
        if e - s >= 500:
            spans.append((s, e, "motion", "physical", 0.6))

    for role in ("physical", "system"):
        media = getattr(p, role)
        if not media.has_audio:
            continue
        start = p.sync.physical_ms(0) if role == "physical" else p.sync.system_ms(0)
        env = syncalgo.onset_envelope(audio_samples(ctx.proxy(role), sr=8000, start_ms=start, dur_ms=dur), 8000)
        if len(env) == 0:
            continue
        thresh = env.mean() + 4 * env.std()
        peaks = np.flatnonzero((env > thresh) & (env >= np.roll(env, 1)) & (env >= np.roll(env, -1)))
        last = -10_000
        for idx in peaks:
            ms = int(idx * 10)
            if ms - last > 400:
                spans.append((ms, ms + 200, "audio-peak", "both", 0.8))
                last = ms
    for role in ("system", "physical"):
        start = p.sync.physical_ms(0) if role == "physical" else p.sync.system_ms(0)
        for cut in _scenedetect(ctx, role, start, dur):
            spans.append((cut, cut + 250, f"scene-cut:{role}", role, 0.7))

    spans.sort()
    windows: list[CandidateWindow] = []
    views: list[set[str]] = []
    for s, e, sig, view, strength in spans:
        s, e = max(0, s - PAD_MS), min(dur, e + PAD_MS)
        last = windows[-1] if windows else None
        if last and s <= last.end_ms and (max(e, last.end_ms) - last.start_ms) <= MAX_WINDOW_MS:
            last.end_ms = max(last.end_ms, e)
            if sig not in last.signals:
                last.signals.append(sig)
            last.strength = max(last.strength, strength)
            views[-1].add(view)
        else:
            windows.append(CandidateWindow(start_ms=s, end_ms=e, signals=[sig], strength=strength))
            views.append({view})
    for w, seen in zip(windows, views):
        seen.discard("both")
        w.view_hint = seen.pop() if len(seen) == 1 else "both"
    p.windows = windows
    short = sum(1 for w in windows if w.end_ms - w.start_ms <= 4000)
    return f"{len(windows)} candidate windows ({short} short, for fine analysis)"


STAGE = Stage("windows", "Candidate action windows", "1", _inputs, run)
