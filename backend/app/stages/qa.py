"""20. Automated QA of the final render.

Pixel statistics cannot prove that a button is readable or that a placement matches its narration,
so QA also exports one representative frame per layout change for human inspection.
"""

from __future__ import annotations

import re
import struct
import subprocess
from pathlib import Path

from .. import syncalgo
from ..captions_fmt import parse_srt
from ..config import settings
from ..media import decode_errors, describe, extract_frame, gray_frames
from ..models import QACheck, QAReport
from ..pipeline import Ctx, Stage
from ..renderer import frames
from ..scheduler import GAP_MS
from .render import FINAL_NAME, build_props

COMPLETION_WORDS = re.compile(r"\b(complete[ds]?|done|finished|success\w*|confirmed|saved|printed|posted|closed)\b", re.I)
# keep in sync with render/src/Final.tsx
CONTENT_W, CONTENT_H, SYSTEM_SHARE = 1920 - 96 - 24, 1080 - 132 - 176, 0.66


def _inputs(p):
    return {"render": p.stages.get("render") and p.stages["render"].input_hash}


def faststart(path: Path) -> bool:
    with path.open("rb") as f:
        while True:
            head = f.read(8)
            if len(head) < 8:
                return False
            size, kind = struct.unpack(">I4s", head)
            if kind == b"moov":
                return True
            if kind == b"mdat":
                return False
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0] - 8
            f.seek(size - 8, 1)


def freezes(path: Path, *, min_s: float = 2.0) -> list[tuple[int, int]]:
    proc = subprocess.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-i", str(path), "-an", "-vf",
                           f"freezedetect=n=0.002:d={min_s}", "-f", "null", "-"], capture_output=True)
    text = proc.stderr.decode(errors="replace")
    starts = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", text)]
    ends = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", text)]
    return [(int(s * 1000), int(e * 1000)) for s, e in zip(starts, ends + [None] * (len(starts) - len(ends))) if e]


def run(ctx: Ctx) -> str:
    p = ctx.project
    final = ctx.out / FINAL_NAME
    checks: list[QACheck] = []

    def check(name: str, ok: bool, detail: str = "", severity: str = "error") -> None:
        checks.append(QACheck(name=name, ok=bool(ok), detail=detail, severity=severity))

    errs = decode_errors(final)
    check("plays back (full strict decode)", not errs, "; ".join(errs[:3]) or "decoded without errors")
    info = describe(final)
    expected_ms = p.timeline.duration_ms
    check("resolution 1920x1080", (info["width"], info["height"]) == (1920, 1080), f"{info['width']}x{info['height']}")
    check(f"frame rate {settings.fps}", abs(info["fps"] - settings.fps) < 0.05, f"{info['fps']} fps")
    check("pixel format yuv420p", info["pix_fmt"] == "yuv420p", info["pix_fmt"])
    check("narration audio stream present", info["has_audio"], info["audio_codec"] or "missing")
    check("duration matches timeline", abs(info["duration_ms"] - expected_ms) <= 1000 / settings.fps + 80,
          f"{info['duration_ms']} ms vs {expected_ms} ms")
    if info["nb_frames"]:
        exp_frames = frames(expected_ms, settings.fps)
        check("frame count", abs(info["nb_frames"] - exp_frames) <= 2, f"{info['nb_frames']} vs {exp_frames}")
    check("fast-start (moov before mdat)", faststart(final), "web playback starts before full download")

    # --- synchronized views: every rendered segment keeps the verified offset; nothing retimed
    props = build_props(ctx, draft=False)
    lag_frames = frames(p.sync.lag_ms, settings.fps)
    bad = [s for s in props["segments"] if s["kind"] == "source"
           and abs((s["physicalStartFrame"] - s["systemStartFrame"]) - lag_frames) > 1]
    check("views synchronized in every segment", not bad, f"lag {p.sync.lag_ms} ms in {len(props['segments'])} segments")
    src_total = sum(s.duration_ms for s in p.timeline.segments if s.kind == "source")
    cut_total = sum(min(c.end_ms, p.sync.shared_duration_ms) - max(c.start_ms, 0) for c in p.timeline.cuts)
    check("no speed change (output source time == shared time - cuts)",
          abs(src_total - (p.sync.shared_duration_ms - max(0, cut_total))) <= 2, f"{src_total} ms of source footage")
    unconfirmed = [c.label for c in p.sync.checks if not c.ok]
    check("sync landmarks verified (start/middle/end)", not unconfirmed or p.sync.manual,
          "manual offset set by reviewer" if p.sync.manual else (f"unconfirmed: {', '.join(unconfirmed)}" if unconfirmed else "all confirmed"),
          severity="warning")

    # --- narration
    live = sorted((c for c in p.cues if not c.dropped and c.out_start_ms is not None), key=lambda c: c.out_start_ms)
    overlaps = [f"{a.id}/{b.id}" for a, b in zip(live, live[1:]) if a.out_end_ms + GAP_MS > b.out_start_ms]
    check("narration clips do not overlap", not overlaps, ", ".join(overlaps[:5]) or f"{len(live)} clips")
    crossing = []
    for c in live:
        seg = p.timeline.segment_at_output(c.out_start_ms)
        if seg is None or seg.kind != "source" or c.out_end_ms > seg.out_end_ms:
            crossing.append(c.id)
    check("no narration crosses a cut or the intro", not crossing, ", ".join(crossing[:5]))
    unsupported = []
    for c in live:
        e = p.event(c.event_id)
        if e is None or e.rejected:
            unsupported.append(f"{c.id}: event missing")
        elif e.evidence_status not in ("demonstrated", "partial") and not e.human_edited:
            unsupported.append(f"{c.id}: event {e.evidence_status or 'not linked to SOP'}")
    check("every cue narrates a demonstrated or reviewer-placed event", not unsupported, "; ".join(unsupported[:5]),
          severity="warning")
    claims = []
    for c in live:
        e = p.event(c.event_id)
        text = c.spoken_text or c.text
        if not e or not (COMPLETION_WORDS.search(text) or c.kind == "result"):
            continue
        # a reviewer who placed the action on the footage is the evidence; otherwise the model must
        # have seen a visible result
        evidence = e.human_edited or (e.kind == "result" and (e.ocr or e.visible_text or e.confidence >= 0.6))
        if not evidence:
            claims.append(f"{c.id}: “{text}” has no visible completion evidence")
        elif c.out_start_ms < (c.anchor_out_ms or 0) and c.kind == "result":
            claims.append(f"{c.id}: result narrated before it is visible")
    check("completion claims supported by footage", not claims, "; ".join(claims[:5]))

    # --- captions match measured schedule
    srt = parse_srt((ctx.work / "captions" / "captions.srt").read_text(encoding="utf-8"))
    by_cue: dict[str, list] = {}
    for cap in p.captions:
        by_cue.setdefault(cap.cue_id, []).append(cap)
    mismatched = [c.id for c in live if c.id not in by_cue or by_cue[c.id][0].start_ms != c.out_start_ms
                  or by_cue[c.id][-1].end_ms != c.out_end_ms]
    check("captions match narration timing", not mismatched and len(srt) == len(p.captions),
          f"{len(srt)} SRT entries; mismatched cues: {', '.join(mismatched[:5]) or 'none'}")

    # --- WISE readability during system actions (geometry; frames exported for human check)
    scale = min(CONTENT_W * SYSTEM_SHARE / max(1, p.system.width), CONTENT_H / max(1, p.system.height))
    rendered_h = round(p.system.height * min(1.0, scale))
    check("WISE readable when emphasised", scale >= 0.45 or rendered_h >= 700,
          f"WISE shown at {scale:.0%} ({round(p.system.width * min(1, scale))}x{rendered_h})", severity="warning")
    qa_dir = ctx.out / "qa_frames"
    qa_dir.mkdir(exist_ok=True)
    for old in qa_dir.glob("*.jpg"):
        old.unlink()
    for i, k in enumerate(p.layout[:40]):
        mid = (k.out_start_ms + k.out_end_ms) // 2
        extract_frame(final, mid, qa_dir / f"{i:02d}_{k.focus}_{mid}ms.jpg", max_width=1280)
    check("representative frames exported for human inspection", True, f"{min(40, len(p.layout))} frames in qa_frames/",
          severity="info")

    # --- frozen footage
    intro_end = next((s.out_end_ms for s in p.timeline.segments if s.kind == "intro"), 0)
    frozen = []
    for s, e in freezes(final):
        if e <= intro_end + 200:
            continue
        shared = p.timeline.to_shared(max(s, intro_end))
        if shared is None:
            continue
        # judge the interior only: detector edges overlap the motion that ends the freeze
        inner_start, inner_dur = shared + 300, (e - max(s, intro_end)) - 600
        if inner_dur < 500:
            continue
        moving = False
        for role in ("physical", "system"):
            src = p.sync.physical_ms(inner_start) if role == "physical" else p.sync.system_ms(inner_start)
            fr = gray_frames(ctx.proxy(role), fps=4, start_ms=src, dur_ms=inner_dur)
            moving |= float(syncalgo.activity_mask(fr, threshold=1.0).mean() if len(fr) else 0) > 0.25
        if moving:
            frozen.append(f"{s/1000:.1f}-{e/1000:.1f}s")
    check("no frozen footage (output static while source moves)", not frozen, ", ".join(frozen[:5]) or "none")

    # --- provenance
    trace = all([p.physical.sha256, p.system.sha256, p.sop.sha256]) and "analyze" in p.provenance
    check("outputs traceable to source hashes and model versions", trace, "see manifest.json")

    report = QAReport(target=FINAL_NAME, checks=checks)
    p.qa = [r for r in p.qa if r.target != FINAL_NAME] + [report]
    (ctx.out / "qa_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    errors = [c for c in checks if not c.ok and c.severity == "error"]
    warnings = [c for c in checks if not c.ok and c.severity == "warning"]
    return f"{len(checks)} checks: {len(errors)} errors, {len(warnings)} warnings" + (
        " — " + "; ".join(c.name for c in errors) if errors else "")


STAGE = Stage("qa", "Quality checks", "1", _inputs, run)
