"""1. Validate uploads: readable, decodable, has video, audio presence, hashes. Originals are never touched."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ..media import decode_errors, describe
from ..pipeline import Ctx, Stage
from ..store import sha256_file


def _inputs(p):
    return {
        "physical": p.physical and p.physical.filename,
        "system": p.system and p.system.filename,
        "sop": p.sop and p.sop.filename,
        "sha": [p.physical and p.physical.sha256, p.system and p.system.sha256, p.sop and p.sop.sha256],
    }


def run(ctx: Ctx) -> str:
    p = ctx.project
    if not (p.physical and p.system and p.sop):
        raise ValueError("project needs a physical recording, a system recording and an SOP .md file")

    problems: list[str] = []
    # The full decode checks are the slow part (4K): run both recordings at once.
    pool = ThreadPoolExecutor(max_workers=2)
    checks = {m.role: pool.submit(decode_errors, ctx.originals / m.filename) for m in (p.physical, p.system)}
    pool.shutdown(wait=False)
    for media in (p.physical, p.system):
        path = ctx.originals / media.filename
        media.sha256 = sha256_file(path)
        try:
            info = describe(path)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"{media.role} recording cannot be read: {exc}") from exc
        if not info["has_video"]:
            raise ValueError(f"{media.role} recording has no video stream")
        media.duration_ms = info["duration_ms"]
        media.width, media.height = info["width"], info["height"]
        media.fps, media.vfr = info["fps"], info["vfr"]
        media.has_audio = info["has_audio"]
        media.audio_sample_rate = info["audio_sample_rate"]
        media.video_codec = info["video_codec"]
        media.decode_errors = checks[media.role].result()
        if media.decode_errors:
            problems.append(f"{media.role}: {media.decode_errors[0]}")
        if media.duration_ms < 1000:
            raise ValueError(f"{media.role} recording is shorter than one second")

    sop_path = ctx.originals / p.sop.filename
    text = sop_path.read_bytes()
    p.sop.sha256 = sha256_file(sop_path)
    try:
        decoded = text.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("SOP file is not UTF-8 text") from exc
    if not decoded.strip():
        raise ValueError("SOP file is empty")
    p.sop.title = next((l.lstrip("# ").strip() for l in decoded.splitlines() if l.startswith("#")), p.title)

    if problems:
        # Hard failure: corrupt media must fail before any expensive AI stage.
        raise ValueError("decode errors — " + "; ".join(problems))

    audio = {m.role: m.has_audio for m in (p.physical, p.system)}
    sync_mode = "audio" if all(audio.values()) else "brightness"
    ctx.provenance("validate", sync_mode=sync_mode)
    missing = [r for r, has in audio.items() if not has]
    note = f"; no audio in {', '.join(missing)} -> brightness sync" if missing else "; both have audio"
    return f"physical {p.physical.duration_ms/1000:.1f}s, system {p.system.duration_ms/1000:.1f}s{note}"


STAGE = Stage("validate", "Validate uploads", "1", _inputs, run)
