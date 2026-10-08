"""7. Timestamped transcript of the original audio (faster-whisper). Context only — never proof."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..config import settings
from ..media import ffmpeg
from ..models import TranscriptSegment, TranscriptWord
from ..pipeline import Ctx, Skip, Stage


def _inputs(p):
    return {"sha": [p.physical.sha256, p.system.sha256], "audio": [p.physical.has_audio, p.system.has_audio],
            "sync": [p.sync.lag_ms, p.sync.shared_start_physical_ms, p.sync.shared_duration_ms],
            "model": settings.asr_model}


def _overlap(a: TranscriptSegment, b: TranscriptSegment) -> float:
    inter = max(0, min(a.end_ms, b.end_ms) - max(a.start_ms, b.start_ms))
    return inter / max(1, min(a.end_ms - a.start_ms, b.end_ms - b.start_ms))


def run(ctx: Ctx) -> str:
    p = ctx.project
    roles = [m.role for m in (p.physical, p.system) if m.has_audio]
    if not roles:
        p.transcript, p.transcript_status = [], "no audio"
        raise Skip("neither recording has audio")
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        p.transcript, p.transcript_status = [], "faster-whisper not installed"
        raise Skip("faster-whisper not installed (uv sync --extra asr)") from exc

    model = WhisperModel(settings.asr_model, device="auto", compute_type="int8")
    found: dict[str, list[TranscriptSegment]] = {}
    for role in roles:
        src_start = p.sync.physical_ms(0) if role == "physical" else p.sync.system_ms(0)
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            ffmpeg("-ss", f"{src_start/1000:.3f}", "-t", f"{p.sync.shared_duration_ms/1000:.3f}",
                   "-i", str(ctx.originals / getattr(p, role).filename), "-vn", "-ac", "1", "-ar", "16000", str(wav))
            segments, _info = model.transcribe(str(wav), word_timestamps=True, vad_filter=True)
            segs = []
            for s in segments:
                words = [TranscriptWord(role=role, start_ms=int(w.start * 1000), end_ms=int(w.end * 1000),
                                        text=w.word.strip(), prob=round(w.probability, 3)) for w in (s.words or [])]
                if s.text.strip():
                    segs.append(TranscriptSegment(role=role, start_ms=int(s.start * 1000), end_ms=int(s.end * 1000),
                                                  text=s.text.strip(), words=words))
            found[role] = segs

    # Both recorders often hear the same speech: keep physical, add only non-duplicate system speech.
    merged = list(found.get("physical", []))
    for seg in found.get("system", []):
        if not any(_overlap(seg, m) > 0.5 for m in merged):
            merged.append(seg)
    p.transcript = sorted(merged, key=lambda s: s.start_ms)
    p.transcript_status = "ok" if p.transcript else "no speech detected"
    ctx.provenance("transcribe", engine="faster-whisper", model=settings.asr_model)
    return f"{len(p.transcript)} speech segments"


STAGE = Stage("transcribe", "Transcribe original audio", "1", _inputs, run)
