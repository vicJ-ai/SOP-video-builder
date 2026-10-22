"""1. Validate uploads: readable, decodable, has video, audio presence, hashes. Originals are never touched."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ..media import decode_errors, describe
from ..pipeline import Ctx, Stage
from ..sopparse import parse_intro
from ..store import sha256_file


def _inputs(p):
    if p.mode == "demo":
        return {"demo": [[m.role, m.filename, m.sha256] for m in p.sources()],
                "notes": p.notes and [p.notes.filename, p.notes.sha256]}
    return {
        "physical": p.physical and p.physical.filename,
        "system": p.system and p.system.filename,
        "sop": p.sop and p.sop.filename,
        "sha": [p.physical and p.physical.sha256, p.system and p.system.sha256, p.sop and p.sop.sha256],
    }


def _probe(ctx: Ctx, medias) -> list[str]:
    """Hash and describe each recording; returns decode problems (raises on unreadable media)."""
    problems: list[str] = []
    # The full decode checks are the slow part (4K): run both recordings at once.
    pool = ThreadPoolExecutor(max_workers=2)
    checks = {m.role: pool.submit(decode_errors, ctx.originals / m.filename) for m in medias}
    pool.shutdown(wait=False)
    for media in medias:
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
    return problems


def run(ctx: Ctx) -> str:
    p = ctx.project
    if p.mode == "demo":
        return _run_demo(ctx)
    if not (p.physical and p.system and p.sop):
        raise ValueError("project needs a physical recording, a system recording and an SOP .md file")

    problems = _probe(ctx, (p.physical, p.system))
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


def _run_demo(ctx: Ctx) -> str:
    p = ctx.project
    medias = p.sources()
    if len(medias) != 1:
        raise ValueError("a demo video needs exactly one recording")
    problems = _probe(ctx, medias)
    if p.notes:
        path = ctx.originals / p.notes.filename
        p.notes.sha256 = sha256_file(path)
        try:
            text = path.read_bytes().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("notes file is not UTF-8 text") from exc
        if not text.strip():
            p.notes = None
        else:
            p.notes.title = next((l.lstrip("# ").strip() for l in text.splitlines() if l.startswith("#")), "")
            paragraphs = [b.strip() for b in text.split("\n\n") if b.strip() and not b.lstrip().startswith(("#", "-", "*", "1."))]
            p.notes.purpose = (parse_intro(text) or (paragraphs[0] if paragraphs else ""))[:420]
    if problems:
        raise ValueError("decode errors — " + "; ".join(problems))
    m = medias[0]
    ctx.provenance("validate", sync_mode="single")
    kind = "screen recording" if m.role == "system" else "camera video"
    return f"{kind} {m.duration_ms/1000:.1f}s" + ("; with notes" if p.notes else "; no notes")


STAGE = Stage("validate", "Validate uploads", "1", _inputs, run)
