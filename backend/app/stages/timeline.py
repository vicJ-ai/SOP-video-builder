"""5. Canonical editing timeline + source-time map (OpenTimelineIO export).

Every event, narration cue, caption and layout key is stored in shared time and projected into
output time through ``Timeline.to_output`` — so adding an intro or cutting footage moves all of
them consistently, from one place.
"""

from __future__ import annotations

import csv
import json

from ..models import Segment, Timeline
from ..pipeline import Ctx, Stage


def _inputs(p):
    return {
        "lag": p.sync.lag_ms, "start": p.sync.shared_start_physical_ms, "dur": p.sync.shared_duration_ms,
        "intro": p.timeline.intro_ms, "cuts": [c.model_dump() for c in p.timeline.cuts],
        "title": p.timeline.title_ms, "outro": p.timeline.outro_ms,
    }


def build_segments(timeline: Timeline, shared_duration_ms: int) -> None:
    cuts = sorted(
        ((max(0, c.start_ms), min(shared_duration_ms, c.end_ms)) for c in timeline.cuts if c.end_ms > c.start_ms),
    )
    merged: list[list[int]] = []
    for s, e in cuts:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        elif e > s:
            merged.append([s, e])

    segments: list[Segment] = []
    out = 0
    if timeline.intro_ms > 0:
        segments.append(Segment(kind="intro", out_start_ms=0, out_end_ms=timeline.intro_ms))
        out = timeline.intro_ms
    if timeline.title_ms:
        segments.append(Segment(kind="title", out_start_ms=out, out_end_ms=out + timeline.title_ms))
        out += timeline.title_ms
    cursor = 0
    for s, e in merged + [[shared_duration_ms, shared_duration_ms]]:
        if s > cursor:
            segments.append(Segment(kind="source", out_start_ms=out, out_end_ms=out + s - cursor, shared_start_ms=cursor))
            out += s - cursor
        cursor = max(cursor, e)
    if timeline.outro_ms:
        segments.append(Segment(kind="outro", out_start_ms=out, out_end_ms=out + timeline.outro_ms))
        out += timeline.outro_ms
    # Resolve every boundary once on the 30 fps frame grid shared by renderer and captions.
    for seg in segments:
        seg.from_frame = round(seg.out_start_ms * 30 / 1000)
        seg.duration_frames = round(seg.out_end_ms * 30 / 1000) - seg.from_frame
    timeline.segments = segments
    timeline.duration_ms = out


def source_time_map(ctx: Ctx) -> list[dict]:
    p = ctx.project
    rows = []
    for seg in p.timeline.segments:
        row = {"kind": seg.kind, "out_start_ms": seg.out_start_ms, "out_end_ms": seg.out_end_ms,
               "shared_start_ms": None, "physical_start_ms": None, "system_start_ms": None}
        if seg.kind == "source":
            row.update(shared_start_ms=seg.shared_start_ms,
                       physical_start_ms=p.sync.physical_ms(seg.shared_start_ms),
                       system_start_ms=p.sync.system_ms(seg.shared_start_ms))
        rows.append(row)
    return rows


def _export_otio(ctx: Ctx, path) -> bool:
    try:
        import opentimelineio as otio
    except ImportError:
        return False
    p = ctx.project
    rate = 1000.0  # integer milliseconds

    def rt(ms: int):
        return otio.opentime.RationalTime(ms, rate)

    tl = otio.schema.Timeline(name=p.title)
    tl.metadata["sop_video_builder"] = {"lag_ms": p.sync.lag_ms, "project": p.id}
    for role in ("physical", "system"):
        media = getattr(p, role)
        track = otio.schema.Track(name=role, kind=otio.schema.TrackKind.Video)
        ref = otio.schema.ExternalReference(
            target_url=(ctx.originals / media.filename).as_uri(),
            available_range=otio.opentime.TimeRange(rt(0), rt(media.duration_ms)),
        )
        for seg in p.timeline.segments:
            if seg.kind != "source":
                track.append(otio.schema.Gap(source_range=otio.opentime.TimeRange(rt(0), rt(seg.duration_ms)),
                                             name=seg.kind))
                continue
            start = p.sync.physical_ms(seg.shared_start_ms) if role == "physical" else p.sync.system_ms(seg.shared_start_ms)
            track.append(otio.schema.Clip(name=f"{role}@{seg.shared_start_ms}", media_reference=ref.clone(),
                                          source_range=otio.opentime.TimeRange(rt(start), rt(seg.duration_ms))))
        tl.tracks.append(track)
    otio.adapters.write_to_file(tl, str(path))
    return True


def run(ctx: Ctx) -> str:
    p = ctx.project
    build_segments(p.timeline, p.sync.shared_duration_ms)
    out = ctx.work / "timeline"
    out.mkdir(exist_ok=True)
    rows = source_time_map(ctx)
    (out / "source_time_map.json").write_text(json.dumps(rows, indent=2))
    with (out / "source_time_map.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    otio_ok = _export_otio(ctx, out / "timeline.otio")
    cut_ms = sum(s.duration_ms for s in p.timeline.segments if s.kind == "source")
    return (f"{len(p.timeline.segments)} segments, output {p.timeline.duration_ms/1000:.1f}s "
            f"({p.sync.shared_duration_ms - cut_ms} ms cut){'' if otio_ok else ' — OTIO not installed'}")


STAGE = Stage("timeline", "Canonical timeline", "2", _inputs, run)
