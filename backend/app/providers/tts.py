"""Text-to-speech adapters. One WAV per narration cue; duration is measured afterwards with ffprobe.

* openai — Qwen3-TTS (or any server) exposing POST /v1/audio/speech, fixed approved preset voice
* kokoro — Kokoro-82M locally on CPU (Apache-2.0), English fallback
* system — the OS speech engine (Windows SAPI via pyttsx3) for quick offline drafts
* edge   — Microsoft Edge neural voice (default Jenny); online, no API key (uv sync --extra neural-tts)
* mock   — silence sized at ~2.6 words/s, for tests and dry runs
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

import httpx

from ..config import settings
from ..media import ffmpeg

WORDS_PER_SECOND = 2.6


class Tts:
    name = "mock"

    def voice_id(self) -> str:
        return f"{self.name}:{settings.tts_voice}"

    def synthesize(self, text: str, out: Path) -> None:
        seconds = max(0.6, len(text.split()) / WORDS_PER_SECOND + 0.2)
        ffmpeg("-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{seconds:.3f}", "-c:a", "pcm_s16le", str(out))


class OpenAITts(Tts):
    name = "openai"

    def voice_id(self) -> str:
        return f"openai:{settings.tts_model}:{settings.tts_voice}"

    def synthesize(self, text: str, out: Path) -> None:
        resp = httpx.post(
            f"{settings.tts_base_url.rstrip('/')}/audio/speech",
            headers={"Authorization": f"Bearer {settings.tts_api_key}"},
            json={"model": settings.tts_model, "voice": settings.tts_voice, "input": text, "response_format": "wav"},
            timeout=180,
        )
        resp.raise_for_status()
        raw = out.with_suffix(".raw.wav")
        raw.write_bytes(resp.content)
        _normalise(raw, out)


class KokoroTts(Tts):
    name = "kokoro"
    _pipe = None

    def voice_id(self) -> str:
        return f"kokoro:{settings.tts_voice}"

    def synthesize(self, text: str, out: Path) -> None:
        import numpy as np
        import soundfile as sf
        from kokoro import KPipeline

        if KokoroTts._pipe is None:
            KokoroTts._pipe = KPipeline(lang_code="a")
        voice = settings.tts_voice if settings.tts_voice.startswith(("af_", "am_", "bf_", "bm_")) else "am_michael"
        chunks = [audio for _, _, audio in KokoroTts._pipe(text, voice=voice)]
        raw = out.with_suffix(".raw.wav")
        sf.write(raw, np.concatenate(chunks), 24000)
        _normalise(raw, out)


class SystemTts(Tts):
    name = "system"

    def synthesize(self, text: str, out: Path) -> None:
        raw = out.with_suffix(".raw.wav")
        script = (
            "import sys,pyttsx3\ne=pyttsx3.init()\ne.setProperty('rate',175)\n"
            "e.save_to_file(sys.argv[1], sys.argv[2])\ne.runAndWait()\n"
        )
        import sys

        subprocess.run([sys.executable, "-c", script, text, str(raw)], check=True, capture_output=True, timeout=120)
        _normalise(raw, out)


class EdgeTts(Tts):
    name = "edge"

    def __init__(self) -> None:
        if not re.fullmatch(r"en-[A-Z]{2}-[A-Za-z]+Neural", settings.tts_edge_voice):
            raise ValueError("TTS_EDGE_VOICE must be an English neural voice, e.g. en-US-JennyNeural")
        for name, value, unit in (("RATE", settings.tts_edge_rate, "%"),
                                  ("PITCH", settings.tts_edge_pitch, "Hz"),
                                  ("VOLUME", settings.tts_edge_volume, "%")):
            if not re.fullmatch(r"[+-]\d+" + re.escape(unit), value) or not -50 <= int(value[:-len(unit)]) <= 50:
                raise ValueError(f"TTS_EDGE_{name} must be a signed integer from -50 to +50{unit}, e.g. +0{unit}")

    def voice_id(self) -> str:
        return f"edge:{settings.tts_edge_voice}:{settings.tts_edge_rate}:{settings.tts_edge_pitch}:{settings.tts_edge_volume}"

    def synthesize(self, text: str, out: Path) -> None:
        try:
            import edge_tts
        except ImportError as exc:
            raise RuntimeError("Jenny voice needs edge-tts: run `uv sync --extra neural-tts` in backend/") from exc
        raw = out.with_suffix(".raw.mp3")

        async def generate() -> None:
            speech = edge_tts.Communicate(text, voice=settings.tts_edge_voice, rate=settings.tts_edge_rate,
                                          pitch=settings.tts_edge_pitch, volume=settings.tts_edge_volume)
            await asyncio.wait_for(speech.save(str(raw)), timeout=90)

        try:
            asyncio.run(generate())
        except Exception as exc:
            raw.unlink(missing_ok=True)
            # Transport errors can embed signed WebSocket URLs; report only the status or type.
            status = getattr(exc, "status", None)
            reason = f"HTTP {status}" if isinstance(status, int) else type(exc).__name__
            raise RuntimeError(f"Edge speech service unavailable ({reason}). Check internet access and retry.") from None
        _normalise(raw, out)


def _normalise(raw: Path, out: Path) -> None:
    """Trim leading/trailing silence, loudness-normalise, 24 kHz mono — keeps measured durations honest."""
    ffmpeg("-i", str(raw), "-af",
           "silenceremove=start_periods=1:start_threshold=-50dB:stop_periods=-1:stop_threshold=-50dB:stop_duration=0.3,"
           "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(out))
    raw.unlink(missing_ok=True)


def get_tts() -> Tts:
    return {"openai": OpenAITts, "kokoro": KokoroTts, "system": SystemTts, "edge": EdgeTts}.get(settings.tts_provider, Tts)()
