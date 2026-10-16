"""Runtime settings, read from environment / backend/.env."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:  # pragma: no cover
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _find_tool(name: str, env_name: str) -> str:
    explicit = _env(env_name)
    if explicit:
        return explicit
    found = shutil.which(name)
    if found:
        return found
    # winget installs FFmpeg under the user's package dir without always refreshing PATH.
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if winget.exists():
        for hit in winget.glob(f"Gyan.FFmpeg*/*/bin/{name}.exe"):
            return str(hit)
    return name


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("SOP_DATA_DIR") or REPO_ROOT / "projects"))
    ffmpeg: str = field(default_factory=lambda: _find_tool("ffmpeg", "FFMPEG_BIN"))
    ffprobe: str = field(default_factory=lambda: _find_tool("ffprobe", "FFPROBE_BIN"))

    # Vision-language model (Qwen3-VL) behind any OpenAI-compatible endpoint, e.g. vLLM:
    #   vllm serve Qwen/Qwen3-VL-8B-Instruct --port 8000
    vlm_provider: str = field(default_factory=lambda: _env("VLM_PROVIDER", "mock"))  # openai | mock
    vlm_base_url: str = field(default_factory=lambda: _env("VLM_BASE_URL", "http://localhost:8000/v1"))
    vlm_api_key: str = field(default_factory=lambda: _env("VLM_API_KEY", "EMPTY"))
    vlm_model: str = field(default_factory=lambda: _env("VLM_MODEL", "Qwen/Qwen3-VL-8B-Instruct"))
    vlm_coarse_fps: float = field(default_factory=lambda: float(_env("VLM_COARSE_FPS", "1")))
    vlm_fine_fps: float = field(default_factory=lambda: float(_env("VLM_FINE_FPS", "6")))
    vlm_chunk_s: float = field(default_factory=lambda: float(_env("VLM_CHUNK_S", "30")))
    vlm_max_frames: int = field(default_factory=lambda: int(_env("VLM_MAX_FRAMES", "32")))
    mock_events_path: str = field(default_factory=lambda: _env("MOCK_EVENTS_PATH"))

    # Text LLM for SOP matching refinement and narration polishing (optional; deterministic otherwise).
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "none"))  # openai | none
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL") or _env("VLM_BASE_URL", "http://localhost:8000/v1"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY") or _env("VLM_API_KEY", "EMPTY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL") or _env("VLM_MODEL", "Qwen/Qwen3-VL-8B-Instruct"))

    # Text-to-speech: openai (Qwen3-TTS behind /v1/audio/speech) | kokoro | system | mock
    tts_provider: str = field(default_factory=lambda: _env("TTS_PROVIDER", "mock"))
    tts_base_url: str = field(default_factory=lambda: _env("TTS_BASE_URL", "http://localhost:8001/v1"))
    tts_api_key: str = field(default_factory=lambda: _env("TTS_API_KEY", "EMPTY"))
    tts_model: str = field(default_factory=lambda: _env("TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"))
    tts_voice: str = field(default_factory=lambda: _env("TTS_VOICE", "Ryan"))  # fixed approved preset voice
    # Edge neural voice (TTS_PROVIDER=edge) has its own preset; TTS_VOICE stays for the other providers.
    tts_edge_voice: str = field(default_factory=lambda: _env("TTS_EDGE_VOICE", "en-US-JennyNeural"))
    tts_edge_rate: str = field(default_factory=lambda: _env("TTS_EDGE_RATE", "+0%"))
    tts_edge_pitch: str = field(default_factory=lambda: _env("TTS_EDGE_PITCH", "+0Hz"))
    tts_edge_volume: str = field(default_factory=lambda: _env("TTS_EDGE_VOLUME", "+0%"))

    asr_model: str = field(default_factory=lambda: _env("ASR_MODEL", "small"))

    # SkellySync (AGPL-3.0) runs out-of-process from its own interpreter; see scripts/setup.ps1.
    skellysync_python: str = field(
        default_factory=lambda: _env("SKELLYSYNC_PYTHON")
        or str(REPO_ROOT / "tools" / "skellysync" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    )

    renderer: str = field(default_factory=lambda: _env("RENDERER", "remotion"))  # remotion | ffmpeg
    render_dir: Path = field(default_factory=lambda: REPO_ROOT / "render")
    render_concurrency: str = field(default_factory=lambda: _env("RENDER_CONCURRENCY", "50%"))

    fps: int = 30


settings = Settings()
