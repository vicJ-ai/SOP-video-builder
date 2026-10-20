"""Narration scheduling on the output timeline — pure functions, unit-tested.

Hard rules (never violated):
  * previous_end + GAP <= next_start                      (no overlapping clips)
  * a cue lies inside one source segment                  (never crosses a cut or the intro)
  * a result cue starts at/after its result is visible    (no claim before evidence)
  * footage is never frozen or retimed to fit speech
Soft rules (violations are reported for shortening / review):
  * action cues start shortly before or at their action (LEAD .. MAX_LAG)
  * a cue finishes before the next narrated action begins
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Event, NarrationCue, Timeline

GAP_MS = 150
LEAD_MS = 400
MAX_LEAD_MS = 1500
MAX_LAG_MS = 700
NEXT_ACTION_SLACK_MS = 300


@dataclass
class Placement:
    cue_id: str
    start: int | None
    end: int | None
    anchor: int | None
    dropped: bool = False
    overrun_ms: int = 0  # how much the cue exceeds its comfortable window (0 = fits)
    late_ms: int = 0
    crosses_segment: bool = False  # hard violation: would play across a cut / past the footage
    notes: list[str] = field(default_factory=list)


def schedule(cues: list[NarrationCue], events: dict[str, Event], timeline: Timeline, *,
             finalize: bool = False) -> list[Placement]:
    """Greedy placement in anchor order. With ``finalize``, cues that would cross a segment are dropped."""
    items = []
    for c in cues:
        e = events.get(c.event_id)
        anchor = timeline.to_output(e.start_ms) if e else None
        items.append((anchor, c, e))
    items.sort(key=lambda t: (t[0] is None, t[0] or 0))

    anchors = [a for a, _, _ in items if a is not None]
    out: list[Placement] = []
    prev_end = -GAP_MS
    for anchor, cue, ev in items:
        if ev is None or anchor is None:
            out.append(Placement(cue.id, None, None, None, dropped=True,
                                 notes=["event removed" if ev is None else "event falls in a cut range"]))
            continue
        seg = timeline.segment_at_output(anchor)
        ev_end = timeline.to_output(max(ev.start_ms, ev.end_ms - 1))
        if ev_end is None or ev_end < anchor:
            ev_end = seg.out_end_ms
        is_result = cue.kind == "result"
        earliest = max(prev_end + GAP_MS, seg.out_start_ms)
        if is_result:
            earliest = max(earliest, anchor)  # result must already be on screen
        else:
            earliest = max(earliest, anchor - MAX_LEAD_MS)
        preferred = anchor if is_result else anchor - LEAD_MS
        start = max(earliest, preferred)
        latest = max(ev_end, anchor + MAX_LAG_MS) if is_result else anchor + MAX_LAG_MS
        p = Placement(cue.id, start, start + cue.duration_ms, anchor)
        if start > latest:
            p.late_ms = start - latest
            p.notes.append(f"starts {p.late_ms} ms after its action (previous line still speaking)")
        next_anchor = next((a for a in anchors if a > anchor), None)
        seg_end = timeline.extent(seg)  # includes a still hold after the last footage
        limit = seg_end
        if next_anchor is not None:
            limit = min(limit, max(next_anchor + NEXT_ACTION_SLACK_MS, start))
        if p.end > limit:
            p.overrun_ms = p.end - limit
            p.notes.append(f"{p.overrun_ms} ms too long for its window")
        if p.end > seg_end:
            p.crosses_segment = True
            p.notes.append("would play across a cut / past the end of the footage")
            if finalize:
                p.dropped = True
                p.notes.append("dropped: does not fit — shorten the line or adjust the cut")
        if not p.dropped:
            prev_end = p.end
        out.append(p)
    return out


def overlaps(placements: list[Placement]) -> list[tuple[str, str]]:
    live = sorted((p for p in placements if not p.dropped and p.start is not None), key=lambda p: p.start)
    return [(a.cue_id, b.cue_id) for a, b in zip(live, live[1:]) if a.end + 0 > b.start]
