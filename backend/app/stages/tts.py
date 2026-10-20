"""15. Voice every narration line, measure it, and schedule it on the output timeline.

Only a line that would play across a cut is shortened, and only by removing filler words; a line that
still does not fit is dropped and reported (never sped up, never cut mid-instruction, never by freezing footage).
Audio is cached by (text, voice), so editing one line regenerates only that clip.

Narrated videos mute the original audio, so footage before the first narrated action or after the last
line plays as dead air; it is trimmed, keeping a short lead-in and tail. Long quiet waits between lines
(a screen loading) play at 3x. When the closing lines run past the end of the recording, its last frame
is held still (briefly) so they can finish.
"""

from __future__ import annotations

from ..media import audio_duration_ms
from ..narration import shorten
from ..pipeline import Ctx, Stage
from ..providers.tts import get_tts
from ..scheduler import schedule
from ..store import stable_hash
from .timeline import build_segments
from .title_voice import LEAD_MS

MAX_SHORTEN_PASSES = 3
HEAD_PAD_MS = 1500  # footage kept before the first narrated action
TAIL_PAD_MS = 1500  # footage kept after the last line finishes
MIN_TRIM_MS = 2000  # a shorter silence is not worth a cut
MAX_HOLD_MS = 6000  # longest still ending added so the closing lines can finish
PAUSE_MIN_MS = 3000  # a quiet stretch between lines this long is fast-forwarded
PAUSE_PAD_MS = 750  # footage kept at normal speed on each side of it
MIN_FAST_MS = 1500  # a shorter fast-forward is not worth the speed change


def quiet_ranges(p, cues) -> list[list[int]]:
    """Shared-time ranges between narration lines long enough to fast-forward (nothing is said there)."""
    tl = p.timeline
    live = sorted((c for c in cues if not c.dropped and c.out_start_ms is not None), key=lambda c: c.out_start_ms)
    out = []
    for a, b in zip(live, live[1:]):
        if b.out_start_ms - a.out_end_ms < PAUSE_MIN_MS:
            continue
        lo = a.out_end_ms + PAUSE_PAD_MS
        hi = min(b.out_start_ms, b.anchor_out_ms if b.anchor_out_ms is not None else b.out_start_ms) - PAUSE_PAD_MS
        seg = tl.segment_at_output(lo)
        if hi - lo < MIN_FAST_MS or seg is None or seg.kind != "source" or seg is not tl.segment_at_output(hi - 1):
            continue  # too short once padded, or crosses a cut
        out.append([tl.to_shared(lo), tl.to_shared(hi)])
    return out


def _inputs(p):
    return {
        "cues": [[c.id, c.event_id, c.text, c.kind, c.phase] for c in p.cues],
        "events": [[e.id, e.start_ms, e.end_ms] for e in p.live_events()],
        "segments": [s.model_dump() for s in p.timeline.segments],
        "voice": get_tts().voice_id(),
    }


def run(ctx: Ctx) -> str:
    p = ctx.project
    tts = get_tts()
    tts_dir = ctx.work / "tts"
    tts_dir.mkdir(exist_ok=True)
    events = {e.id: e for e in p.live_events()}

    def voice(cue) -> None:
        text = cue.spoken_text or cue.text
        h = stable_hash([text, tts.voice_id()])
        rel = f"tts/{h}.wav"
        path = ctx.work / rel
        if not path.exists():
            tts.synthesize(text, path)
        cue.audio, cue.audio_hash, cue.duration_ms = rel, h, audio_duration_ms(path)

    for cue in p.cues:
        cue.spoken_text, cue.schedule_notes, cue.dropped = "", [], False
        voice(cue)

    # The title + introduction plays on the title card, not on the footage scheduler.
    title_cues = [c for c in p.cues if c.phase == "title"]
    footage_cues = [c for c in p.cues if c.phase == "operational"]

    # Start the footage shortly before the first narrated action (measured on the untrimmed timeline).
    tl, dur = p.timeline, p.sync.shared_duration_ms
    tl.trim_head_ms = tl.trim_tail_ms = tl.hold_ms = 0
    tl.speedups = []
    build_segments(tl, dur)
    starts = [events[c.event_id].start_ms for c in footage_cues if c.event_id in events]
    head = min(starts) - HEAD_PAD_MS if starts else 0
    if head >= MIN_TRIM_MS:
        tl.trim_head_ms = head
        build_segments(tl, dur)

    card = next((s for s in p.timeline.segments if s.kind == "title"), None)
    for cue in title_cues:
        cue.anchor_out_ms = None
        if card is None:
            cue.dropped = True
            cue.schedule_notes = ["no title card in this design"]
            continue
        cue.out_start_ms = card.out_start_ms + LEAD_MS
        cue.out_end_ms = cue.out_start_ms + cue.duration_ms
        if cue.out_end_ms > card.out_end_ms:  # title_voice sizes the card from this same clip
            cue.dropped = True
            cue.schedule_notes = ["title card shorter than the introduction; rerun from title_voice"]

    by_id = {c.id: c for c in footage_cues}
    shortened = 0
    for _ in range(MAX_SHORTEN_PASSES):
        placements = schedule(footage_cues, events, p.timeline)
        # Only a line that would play across a cut / past the footage needs shortening. A line that merely
        # runs into the next action is fine: the next line just starts a little later (reported as timing).
        tight = [pl for pl in placements if not pl.dropped and pl.crosses_segment]
        changed = False
        for pl in tight:
            cue = by_id[pl.cue_id]
            current = cue.spoken_text or cue.text
            shorter = shorten(current)
            if shorter != current and len(shorter.split()) >= 2:
                cue.spoken_text = shorter
                voice(cue)
                changed = True
                shortened += 1
        if not changed:
            break

    def place() -> int:
        late = 0
        for pl in schedule(footage_cues, events, p.timeline, finalize=True):
            cue = by_id[pl.cue_id]
            cue.out_start_ms, cue.out_end_ms, cue.anchor_out_ms = pl.start, pl.end, pl.anchor
            cue.dropped = pl.dropped
            cue.schedule_notes = pl.notes + ([f"shortened from: {cue.text}"] if cue.spoken_text else [])
            if pl.late_ms or pl.overrun_ms:
                late += 1
        return late

    def live_ends() -> list[int]:
        return [c.out_end_ms for c in footage_cues if not c.dropped and c.out_end_ms is not None]

    late = place()
    lost = sum(c.dropped for c in footage_cues)

    # Fast-forward long quiet waits between lines (a screen loading). Keep it only if every line still
    # fits and none plays during a fast stretch; otherwise the video stays at normal speed.
    if fast := quiet_ranges(p, footage_cues):
        tl.speedups = fast
        build_segments(tl, dur)
        late_fast = place()
        fast_segs = [s for s in tl.segments if s.rate != 1]
        talking = any(c.out_start_ms < s.out_end_ms and c.out_end_ms > s.out_start_ms
                      for c in footage_cues if not c.dropped for s in fast_segs)
        if sum(c.dropped for c in footage_cues) > lost or talking:
            tl.speedups = []
            build_segments(tl, dur)
            late = place()
        else:
            late = late_fast

    if lost:
        # Closing lines that run past the recording: hold its last frame still so they can finish.
        tl.hold_ms = MAX_HOLD_MS
        build_segments(tl, dur)
        place()
        if sum(c.dropped for c in footage_cues) < lost:
            last = next(s for s in reversed(tl.segments) if s.kind == "source")
            tl.hold_ms = min(MAX_HOLD_MS, max(0, max(live_ends()) + TAIL_PAD_MS - last.out_end_ms))
        else:
            tl.hold_ms = 0
        build_segments(tl, dur)
        late = place()

    # End the footage shortly after the last line. Only the final stretch shrinks, so nothing already
    # placed moves; scheduling again keeps every check on the final timeline.
    sources = [s for s in tl.segments if s.kind == "source"]
    ends = live_ends()
    if not tl.hold_ms and sources and sources[-1].rate == 1 and ends and max(ends) >= sources[-1].out_start_ms:
        tail = sources[-1].out_end_ms - (max(ends) + TAIL_PAD_MS)
        if tail >= MIN_TRIM_MS:
            tl.trim_tail_ms = tail
            build_segments(tl, dur)
            late = place()

    from ..frameclock import resolve_cues
    resolve_cues(p)  # one 30 fps frame schedule for the renderer, captions and sidecars
    ctx.provenance("tts", provider=tts.name, voice=tts.voice_id(), trim_head_ms=tl.trim_head_ms,
                   trim_tail_ms=tl.trim_tail_ms, hold_ms=tl.hold_ms, speedups=tl.speedups)
    dropped = sum(c.dropped for c in p.cues)
    total = sum(c.duration_ms for c in p.cues if not c.dropped)
    trimmed = (f"; trimmed {tl.trim_head_ms/1000:.1f}s silence at start, {tl.trim_tail_ms/1000:.1f}s at end"
               if tl.trim_head_ms or tl.trim_tail_ms else "")
    trimmed += f"; held the last frame {tl.hold_ms/1000:.1f}s for the closing lines" if tl.hold_ms else ""
    if fast_segs := [s for s in tl.segments if s.rate != 1]:
        trimmed += (f"; fast-forwarded {len(fast_segs)} pause{'s' if len(fast_segs) > 1 else ''} "
                    f"({sum(s.shared_ms for s in fast_segs)/1000:.1f}s -> {sum(s.duration_ms for s in fast_segs)/1000:.1f}s)")
    return (f"{len(p.cues) - dropped} clips ({total/1000:.1f}s speech) via {tts.name}; {shortened} shortened, "
            f"{late} timing warnings, {dropped} dropped{trimmed}")


STAGE = Stage("tts", "Voice & schedule narration", "6", _inputs, run)
