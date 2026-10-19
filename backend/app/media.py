"""Thin FFmpeg / ffprobe helpers. Everything media-related goes through here."""

from __future__ import annotations

import bisect
import json
import os
import re
import subprocess
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

import numpy as np

from .config import settings


class MediaError(RuntimeError):
    pass


def run(args: list[str], *, capture: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=capture, timeout=timeout)
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace")[-2000:] if capture else ""
        raise MediaError(f"{Path(args[0]).name} failed ({proc.returncode}): {err}")
    return proc


def ffmpeg(*args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    return run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", *args], timeout=timeout)


def probe(path: Path) -> dict:
    proc = run(
        [settings.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    )
    return json.loads(proc.stdout)


def _rate(value: str | None) -> float:
    if not value or value in ("0/0", "N/A"):
        return 0.0
    return float(Fraction(value))


def describe(path: Path) -> dict:
    """Summarised stream info used by validation and QA."""
    info = probe(path)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    duration = float(info["format"].get("duration") or (video or {}).get("duration") or 0)
    avg = _rate((video or {}).get("avg_frame_rate"))
    real = _rate((video or {}).get("r_frame_rate"))
    return {
        "duration_ms": int(round(duration * 1000)),
        "format": info["format"].get("format_name", ""),
        "has_video": video is not None,
        "width": int((video or {}).get("width") or 0),
        "height": int((video or {}).get("height") or 0),
        "fps": round(avg or real, 3),
        "vfr": bool(avg and real and abs(avg - real) > 0.01 * real),
        "video_codec": (video or {}).get("codec_name", ""),
        "pix_fmt": (video or {}).get("pix_fmt", ""),
        "nb_frames": int((video or {}).get("nb_frames") or 0),
        "has_audio": audio is not None,
        "audio_codec": (audio or {}).get("codec_name", ""),
        "audio_sample_rate": int((audio or {}).get("sample_rate") or 0),
    }


# Messages about container timestamps, not damaged media. Phone / screen recordings with a variable
# frame rate routinely produce them; the CFR proxy normalises the timing without changing speed.
# GPU video decoding where available (about 2x faster on 4K HEVC); FFmpeg falls back to software itself.
HWACCEL = ["-hwaccel", "d3d11va"] if os.name == "nt" else ["-hwaccel", "auto"]

_BENIGN = re.compile(
    r"non monotonically increasing dts|non-monotonous dts|Last message repeated|"
    r"Past duration .* too large|DTS .* < .* out of order|Timestamps are unset|"
    r"st:\d+ invalid dropping|Packet corrupt.*\(stream = \d+, dts = NOPTS\)", re.I)


def decode_errors(path: Path, *, seconds: float | None = None) -> list[str]:
    """Full strict decode. Fails early on corrupt uploads before any expensive analysis."""
    args = [settings.ffmpeg, "-hide_banner", "-nostdin", "-v", "error", *HWACCEL]
    if seconds:
        args += ["-t", str(seconds)]
    args += ["-i", str(path), "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"]
    proc = subprocess.run(args, capture_output=True)
    lines = [l.strip() for l in proc.stderr.decode(errors="replace").splitlines() if l.strip()]
    lines = [l for l in lines if not _BENIGN.search(l)]
    if proc.returncode != 0 and not lines:
        lines = [f"ffmpeg exited with {proc.returncode}"]
    return lines[:20]


def audio_samples(path: Path, *, sr: int = 8000, start_ms: int = 0, dur_ms: int | None = None) -> np.ndarray:
    """Mono float32 PCM at ``sr`` (decoded by FFmpeg, so any container/codec works)."""
    args = [settings.ffmpeg, "-hide_banner", "-nostdin", "-v", "error"]
    if start_ms:
        args += ["-ss", f"{start_ms / 1000:.3f}"]
    args += ["-i", str(path)]
    if dur_ms is not None:
        args += ["-t", f"{dur_ms / 1000:.3f}"]
    args += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    proc = run(args)
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def gray_frames(
    path: Path, *, fps: float, width: int = 64, height: int = 36, start_ms: int = 0, dur_ms: int | None = None,
    crop: tuple[float, float, float, float] | None = None,
) -> np.ndarray:
    """Tiny grayscale frames as an (N, H, W) uint8 array: cheap signal for diffs/brightness."""
    vf = []
    if crop:
        x, y, w, h = crop
        vf.append(f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y}")
    vf += [f"fps={fps}", f"scale={width}:{height}:flags=area", "format=gray"]
    args = [settings.ffmpeg, "-hide_banner", "-nostdin", "-v", "error"]
    if start_ms:
        args += ["-ss", f"{start_ms / 1000:.3f}"]
    args += ["-i", str(path)]
    if dur_ms is not None:
        args += ["-t", f"{dur_ms / 1000:.3f}"]
    args += ["-an", "-vf", ",".join(vf), "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    raw = run(args).stdout
    n = len(raw) // (width * height)
    return np.frombuffer(raw[: n * width * height], dtype=np.uint8).reshape(n, height, width)


@lru_cache(maxsize=32)
def _frame_times(path: str, mtime_ns: int, size: int) -> tuple[float, ...]:
    proc = run([settings.ffprobe, "-v", "error", "-show_entries", "format=start_time", "-select_streams", "v:0",
                "-show_entries", "packet=pts_time", "-of", "json", path])
    data = json.loads(proc.stdout)
    start = float((data.get("format") or {}).get("start_time") or 0)
    return tuple(sorted(float(p["pts_time"]) - start for p in data.get("packets", []) if p.get("pts_time") not in (None, "N/A")))


def frame_times(path: Path) -> tuple[float, ...]:
    """Presentation time (s, from file start) of every video frame. Cached per file version."""
    stat = path.stat()
    return _frame_times(str(path), stat.st_mtime_ns, stat.st_size)


def extract_frame(path: Path, at_ms: int, out: Path, *, max_width: int | None = None) -> Path:
    """The frame on screen at ``at_ms``: the latest frame at or before it.

    Phone screen recorders only write a frame when the screen changes, so a time can fall in a
    multi-second gap or after the last frame; plain seeking returns the next frame or nothing there.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = f"scale='min({max_width},iw)':-2" if max_width else "null"
    at = max(at_ms, 0) / 1000
    times = frame_times(path)
    if times:
        shown = times[max(bisect.bisect_right(times, at + 1e-6) - 1, 0)]
        at = max(shown - 0.0005, 0)
    ffmpeg("-ss", f"{at:.4f}", "-i", str(path), "-frames:v", "1", "-vf", vf, "-q:v", "2", str(out))
    return out


PROXY_MAX = 1920  # longest side; the final render is 1080p, originals stay full resolution


def make_proxy(src: Path, out: Path, *, fps: int) -> None:
    """Constant-frame-rate H.264 proxy. Keeps timestamps (no speed change), keeps audio."""
    out.parent.mkdir(parents=True, exist_ok=True)
    fit = (f"scale='if(gte(iw,ih),min({PROXY_MAX},iw),-2)':'if(gte(iw,ih),-2,min({PROXY_MAX},ih))'"
           ":flags=bicubic,setsar=1")
    ffmpeg(
        *HWACCEL, "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-vf", fit,
        "-fps_mode", "cfr", "-r", str(fps),
        # one keyframe per second and no B-frames: the renderer seeks into these proxies at every segment
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-g", str(fps), "-keyint_min", str(fps), "-sc_threshold", "0", "-bf", "0",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-movflags", "+faststart", str(out),
    )


def audio_duration_ms(path: Path) -> int:
    return describe(path)["duration_ms"]


def version() -> str:
    try:
        out = run([settings.ffmpeg, "-version"]).stdout.decode().splitlines()[0]
        return out
    except Exception:  # noqa: BLE001
        return "unavailable"
