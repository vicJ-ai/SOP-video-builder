"""21. Deliverables + provenance manifest + editable project snapshot."""

from __future__ import annotations

import csv
import json
import platform
import shutil
import zipfile
from importlib import metadata

from ..config import settings
from ..media import extract_frame, ffmpeg, version as ffmpeg_version
from ..models import now_iso
from ..pipeline import Ctx, Stage
from .render import FINAL_NAME


def _inputs(p):
    return {"render": p.stages.get("render") and p.stages["render"].input_hash,
            "qa": [r.model_dump(exclude={"created_at"}) for r in p.qa]}


def _ts(ms: int | None) -> str:
    if ms is None:
        return ""
    s, ms_ = divmod(ms, 1000)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{ms_:03d}"


def _versions() -> dict:
    out = {"python": platform.python_version(), "ffmpeg": ffmpeg_version()}
    for pkg in ("fastapi", "pydantic", "numpy", "opentimelineio", "faster-whisper", "paddleocr", "scenedetect", "kokoro"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            pass
    try:
        pkg = json.loads((settings.render_dir / "package.json").read_text(encoding="utf-8"))
        out["remotion"] = pkg["dependencies"].get("remotion")
    except Exception:  # noqa: BLE001
        pass
    return out


def coverage_markdown(p) -> str:
    rows = {c.step_id: c for c in p.coverage.steps}
    lines = [f"# SOP coverage — {p.sop.title or p.title}", "", f"SOP file: `{p.sop.filename}` (sha256 `{p.sop.sha256[:12]}`)",
             f"Generated: {now_iso()}", ""]
    counts: dict[str, int] = {}
    for c in p.coverage.steps:
        counts[c.effective] = counts.get(c.effective, 0) + 1
    lines += ["| Status | Steps |", "|---|---:|"] + [f"| {k} | {v} |" for k, v in sorted(counts.items())] + [""]
    gaps = [s for s in p.sop_steps if rows.get(s.id) and rows[s.id].effective != "demonstrated"]
    lines += ["## Steps not fully demonstrated", ""]
    if not gaps:
        lines.append("All SOP steps are demonstrated in the footage.")
    for s in gaps:
        c = rows[s.id]
        note = "; ".join(c.notes) or "no matching action observed"
        override = " (reviewer override)" if c.human_override else ""
        lines.append(f"- **Step {s.number}** — {c.effective}{override}: {s.text}  \n  _{note}_")
    lines += ["", "## Flagged differences between footage and SOP", ""]
    if not p.coverage.discrepancies:
        lines.append("None.")
    for d in p.coverage.discrepancies:
        lines.append(f"- `{d.kind}` {d.message}")
    lines += ["", "## All steps", "", "| Step | Status | Evidence (shared time) | SOP text |", "|---|---|---|---|"]
    for s in p.sop_steps:
        c = rows.get(s.id)
        evs = [p.event(eid) for eid in (c.event_ids if c else [])]
        ev = ", ".join(f"{_ts(e.start_ms)}–{_ts(e.end_ms)}" for e in evs if e) or "—"
        lines.append(f"| {s.number} | {c.effective if c else 'missing'} | {ev} | {s.text.replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def run(ctx: Ctx) -> str:
    p = ctx.project
    out = ctx.out
    final = out / FINAL_NAME
    live = sorted((c for c in p.cues if not c.dropped and c.out_start_ms is not None), key=lambda c: c.out_start_ms)

    # narration transcript + timing table
    (out / "narration_transcript.txt").write_text(
        "\n".join(f"[{_ts(c.out_start_ms)}] {c.spoken_text or c.text}" for c in live) + "\n", encoding="utf-8")
    with (out / "narration_timing.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["cue_id", "event_id", "sop_step_id", "kind", "text", "spoken_text", "start_ms", "end_ms",
                    "duration_ms", "action_at_ms", "shared_event_start_ms", "dropped", "notes"])
        for c in sorted(p.cues, key=lambda c: (c.out_start_ms is None, c.out_start_ms or 0)):
            e = p.event(c.event_id)
            w.writerow([c.id, c.event_id, c.sop_step_id or "", c.kind, c.text, c.spoken_text, c.out_start_ms,
                        c.out_end_ms, c.duration_ms, c.anchor_out_ms, e.start_ms if e else "", c.dropped,
                        " | ".join(c.schedule_notes)])
    for name in ("captions.srt", "captions.vtt"):
        shutil.copy2(ctx.work / "captions" / name, out / name)
    for name in ("source_time_map.csv", "source_time_map.json", "timeline.otio"):
        src = ctx.work / "timeline" / name
        if src.exists():
            shutil.copy2(src, out / name)

    # SOP coverage report
    (out / "sop_coverage_report.md").write_text(coverage_markdown(p), encoding="utf-8")
    (out / "sop_coverage_report.json").write_text(json.dumps({
        "steps": [{**s.model_dump(), "coverage": next((c.model_dump() for c in p.coverage.steps if c.step_id == s.id), None)}
                  for s in p.sop_steps],
        "discrepancies": [d.model_dump() for d in p.coverage.discrepancies]}, indent=2), encoding="utf-8")

    # QA report (markdown)
    qa = next((r for r in p.qa if r.target == FINAL_NAME), None)
    if qa:
        md = [f"# QA report — {FINAL_NAME}", "", f"Result: **{'PASS' if qa.ok else 'FAIL'}**", "",
              "| Check | Result | Detail |", "|---|---|---|"]
        md += [f"| {c.name} | {'ok' if c.ok else c.severity.upper()} | {c.detail.replace('|', '/')} |" for c in qa.checks]
        (out / "qa_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    # poster + compact version
    poster_at = p.layout[0].out_start_ms + 1500 if p.layout else p.timeline.duration_ms // 2
    extract_frame(final, poster_at, out / "poster.jpg")
    ffmpeg("-i", str(final), "-vf", "scale=-2:720", "-c:v", "libx264", "-preset", "medium", "-crf", "26",
           "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out / "final_720p.mp4"))

    # provenance manifest
    manifest = {
        "generated_at": now_iso(), "project_id": p.id, "title": p.title,
        "inputs": {r: {"file": m.filename, "sha256": m.sha256, "duration_ms": m.duration_ms, "size": [m.width, m.height],
                       "fps": m.fps, "has_audio": m.has_audio} for r, m in (("physical", p.physical), ("system", p.system))}
        | {"sop": {"file": p.sop.filename, "sha256": p.sop.sha256, "title": p.sop.title}},
        "footage_in_deliverable": "proxy (constant-frame-rate H.264 of the originals; same timing)",
        "sync": p.sync.model_dump(),
        "timeline": p.timeline.model_dump(),
        "events": [e.model_dump() for e in p.events],
        "transcript": {"status": p.transcript_status, "segments": [s.model_dump(exclude={"words"}) for s in p.transcript]},
        "coverage": p.coverage.model_dump(),
        "narration": [c.model_dump() for c in p.cues],
        "settings": {"vlm": {"provider": settings.vlm_provider, "model": settings.vlm_model,
                             "coarse_fps": settings.vlm_coarse_fps, "fine_fps": settings.vlm_fine_fps},
                     "tts": {"provider": settings.tts_provider, "model": settings.tts_model, "voice": settings.tts_voice},
                     "llm": {"provider": settings.llm_provider, "model": settings.llm_model},
                     "asr": settings.asr_model, "renderer": settings.renderer, "fps": settings.fps},
        "stage_provenance": {k: v for k, v in p.provenance.items() if k != "errors"},
        "versions": _versions(),
        "human_actions": [h.model_dump() for h in p.history],
        "qa": [r.model_dump() for r in p.qa],
        "stages": {k: v.model_dump() for k, v in p.stages.items()},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    (out / "project.json").write_text(p.model_dump_json(indent=2), encoding="utf-8")

    bundle = out / f"{p.id}_deliverables.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(out.rglob("*")):
            if f.is_file() and f != bundle and not f.name.endswith((".props.json", ".log")):
                z.write(f, f.relative_to(out))
    return f"{sum(1 for f in out.iterdir() if f.is_file())} files + {bundle.name}"


STAGE = Stage("package", "Package deliverables", "1", _inputs, run)
