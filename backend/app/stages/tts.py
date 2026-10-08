"""15. Voice every narration line, measure it, and schedule it on the output timeline.

Lines that do not fit their window are shortened (never sped up, never by freezing footage).
Audio is cached by (text, voice), so editing one line regenerates only that clip.
"""

from __future__ import annotations

from ..media import audio_duration_ms
from ..narration import shorten
from ..pipeline import Ctx, Stage
from ..providers.tts import get_tts
from ..scheduler import schedule
from ..store import stable_hash

MAX_SHORTEN_PASSES = 3


def _inputs(p):
    return {
        "cues": [[c.id, c.event_id, c.text, c.kind] for c in p.cues],
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

    by_id = {c.id: c for c in p.cues}
    shortened = 0
    for _ in range(MAX_SHORTEN_PASSES):
        placements = schedule(p.cues, events, p.timeline)
        tight = [pl for pl in placements if not pl.dropped and (pl.overrun_ms > 0 or pl.crosses_segment)]
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

    final = schedule(p.cues, events, p.timeline, finalize=True)
    late = 0
    for pl in final:
        cue = by_id[pl.cue_id]
        cue.out_start_ms, cue.out_end_ms, cue.anchor_out_ms = pl.start, pl.end, pl.anchor
        cue.dropped = pl.dropped
        cue.schedule_notes = pl.notes + ([f"shortened from: {cue.text}"] if cue.spoken_text else [])
        if pl.late_ms or pl.overrun_ms:
            late += 1
    ctx.provenance("tts", provider=tts.name, voice=tts.voice_id())
    dropped = sum(c.dropped for c in p.cues)
    total = sum(c.duration_ms for c in p.cues if not c.dropped)
    return (f"{len(p.cues) - dropped} clips ({total/1000:.1f}s speech) via {tts.name}; {shortened} shortened, "
            f"{late} timing warnings, {dropped} dropped")


STAGE = Stage("tts", "Voice & schedule narration", "1", _inputs, run)
