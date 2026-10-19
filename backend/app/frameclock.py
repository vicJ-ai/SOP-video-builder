"""One canonical frame schedule shared by renderer, captions and delivery sidecars."""
from math import ceil


def resolve_cues(project, fps: int = 30) -> None:
    from .scheduler import GAP_MS
    previous_end = -1
    for cue in sorted(project.cues, key=lambda c: c.out_start_ms if c.out_start_ms is not None else 10**15):
        if cue.dropped or cue.out_start_ms is None:
            cue.from_frame = cue.end_frame = cue.duration_frames = None
            continue
        start = max(round(cue.out_start_ms * fps / 1000), previous_end + ceil(GAP_MS * fps / 1000))
        if cue.kind=="result" and cue.anchor_out_ms is not None:
            start=max(start,ceil(cue.anchor_out_ms*fps/1000))
        duration = max(1, ceil(cue.duration_ms * fps / 1000))
        end = start + duration
        segment = project.timeline.segment_at_output(round(start * 1000 / fps))
        required = {"opener": "intro", "title": "title", "operational": "source"}[cue.phase]
        if not segment or segment.kind != required or end > round(segment.out_end_ms * fps / 1000):
            cue.dropped = True
            cue.schedule_notes.append("measured speech exceeds canonical phase/cut boundary; no speedup")
            continue
        cue.from_frame, cue.end_frame, cue.duration_frames = start, end, duration
        cue.out_start_ms, cue.out_end_ms = round(start * 1000 / fps), round(end * 1000 / fps)
        previous_end = end
