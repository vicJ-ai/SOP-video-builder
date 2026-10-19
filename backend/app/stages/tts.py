"""15. Voice every narration line, measure it, and schedule it on the output timeline.

Only a line that would play across a cut is shortened, and only by removing filler words; a line that
still does not fit is dropped and reported (never sped up, never cut mid-instruction, never by freezing footage).
Audio is cached by (text, voice), so editing one line regenerates only that clip.
"""

from __future__ import annotations

from ..media import audio_duration_ms
from ..narration import shorten
from ..pipeline import Ctx, Stage
from ..providers.tts import get_tts
from ..scheduler import schedule
from ..store import stable_hash
from .title_voice import LEAD_MS

MAX_SHORTEN_PASSES = 3


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

    final = schedule(footage_cues, events, p.timeline, finalize=True)
    late = 0
    for pl in final:
        cue = by_id[pl.cue_id]
        cue.out_start_ms, cue.out_end_ms, cue.anchor_out_ms = pl.start, pl.end, pl.anchor
        cue.dropped = pl.dropped
        cue.schedule_notes = pl.notes + ([f"shortened from: {cue.text}"] if cue.spoken_text else [])
        if pl.late_ms or pl.overrun_ms:
            late += 1
    from ..frameclock import resolve_cues
    resolve_cues(p)  # one 30 fps frame schedule for the renderer, captions and sidecars
    ctx.provenance("tts", provider=tts.name, voice=tts.voice_id())
    dropped = sum(c.dropped for c in p.cues)
    total = sum(c.duration_ms for c in p.cues if not c.dropped)
    return (f"{len(p.cues) - dropped} clips ({total/1000:.1f}s speech) via {tts.name}; {shortened} shortened, "
            f"{late} timing warnings, {dropped} dropped")


STAGE = Stage("tts", "Voice & schedule narration", "3", _inputs, run)
