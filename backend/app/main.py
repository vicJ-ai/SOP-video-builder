"""HTTP API + static review UI.

    uvicorn app.main:app --reload --port 8080      (from backend/)
"""

from __future__ import annotations

import importlib.util
import json
import logging
import shutil
import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import REPO_ROOT, settings
from .models import Cut, EvidenceStatus, Event, HumanAction, Project, SopDoc, SourceMedia, View
from .pipeline import Runner, effective_states, stages
from .renderer import remotion_available
from .stages.analyze import event_id
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="UNIS SOP Video Builder", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
store = Store()
runner = Runner(store)

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mpeg", ".mpg"}


# --------------------------------------------------------------------------- helpers


def _load(pid: str) -> Project:
    try:
        return store.load(pid)
    except KeyError:
        raise HTTPException(404, "project not found") from None


def _editable(pid: str) -> Project:
    # The quick refresh after an edit takes seconds — wait for it rather than refusing the edit.
    if not runner.wait(pid, timeout=45):
        raise HTTPException(409, "the video is rendering — edits are possible again when it finishes")
    return _load(pid)


def _record(p: Project, action: str, **detail) -> None:
    p.history.append(HumanAction(action=action, detail=detail))
    store.save(p)


_refresh_timers: dict[str, threading.Timer] = {}
_refresh_guard = threading.Lock()
REFRESH_DELAY_S = 1.5


def _refresh_soon(pid: str) -> None:
    """After an edit: re-run the cheap stages (SOP match → narration → voice → captions), debounced.

    Stops before the review stop, so the in-browser preview reflects the edit within seconds.
    """
    def fire():
        with _refresh_guard:
            _refresh_timers.pop(pid, None)
        if not runner.start(pid, until="captions"):
            _refresh_soon(pid)  # a run is in progress — try again shortly

    with _refresh_guard:
        if t := _refresh_timers.get(pid):
            t.cancel()
        t = threading.Timer(REFRESH_DELAY_S, fire)
        t.daemon = True
        _refresh_timers[pid] = t
        t.start()


def _edited(p: Project, action: str, **detail) -> dict:
    """Save a reviewer edit: no approvals needed, but a finished render must be re-requested."""
    p.review.approved = False
    _record(p, action, **detail)
    _refresh_soon(p.id)
    return _view(p)


def _view(p: Project) -> dict:
    states = effective_states(p)
    data = json.loads(p.model_dump_json())
    data["stage_order"] = [{"name": s.name, "title": s.title, "gate": s.gate} for s in stages()]
    data["stages"] = {k: v.model_dump() for k, v in states.items()}
    data["running"] = runner.is_running(p.id)
    data["files"] = {
        "preview": "work/preview/analysis.mp4", "draft": "work/draft/draft.mp4", "final": "out/final.mp4",
        "vtt": "work/captions/captions.vtt", "waveforms": "work/sync/waveforms.json",
        "physical": f"work/{p.physical.proxy}" if p.physical and p.physical.proxy else None,
        "system": f"work/{p.system.proxy}" if p.system and p.system.proxy else None,
    }
    data["exists"] = {k: bool(v) and (store.dir(p.id) / v).exists() for k, v in data["files"].items()}
    data["deliverables"] = sorted(f.name for f in store.out(p.id).iterdir() if f.is_file()) if store.out(p.id).exists() else []
    return data


def _summary(p: Project) -> dict:
    states = effective_states(p)
    current = next((n for n, s in states.items() if s.state in ("running", "awaiting_review", "failed")), None)
    done = sum(s.state in ("done", "skipped") for s in states.values())
    return {"id": p.id, "title": p.title, "created_at": p.created_at, "updated_at": p.updated_at,
            "progress": done / max(1, len(states)), "current": current,
            "current_state": states[current].state if current else ("done" if done == len(states) else "pending"),
            "running": runner.is_running(p.id)}


# --------------------------------------------------------------------------- environment


@app.get("/api/health")
def health():
    extras = {name: importlib.util.find_spec(mod) is not None for name, mod in
              (("faster-whisper", "faster_whisper"), ("paddleocr", "paddleocr"), ("scenedetect", "scenedetect"),
               ("kokoro", "kokoro"), ("opentimelineio", "opentimelineio"))}
    return {
        "ffmpeg": Path(settings.ffmpeg).name if shutil.which(settings.ffmpeg) or Path(settings.ffmpeg).exists() else None,
        "remotion": remotion_available(), "renderer": settings.renderer,
        "skellysync": Path(settings.skellysync_python).exists(),
        "vlm": {"provider": settings.vlm_provider, "model": settings.vlm_model, "base_url": settings.vlm_base_url},
        "llm": settings.llm_provider, "tts": {"provider": settings.tts_provider, "voice": settings.tts_voice},
        "extras": extras,
    }


# --------------------------------------------------------------------------- projects


@app.get("/api/projects")
def list_projects():
    return [_summary(p) for p in store.list()]


@app.post("/api/projects")
async def create_project(
    title: str = Form(...), physical: UploadFile = File(...), system: UploadFile = File(...),
    sop: UploadFile = File(...), auto_start: bool = Form(True), auto_approve: bool = Form(False),
):
    for f, kind in ((physical, "physical"), (system, "system")):
        if Path(f.filename or "").suffix.lower() not in VIDEO_EXT:
            raise HTTPException(400, f"{kind} recording must be a video file ({', '.join(sorted(VIDEO_EXT))})")
    if Path(sop.filename or "").suffix.lower() not in (".md", ".markdown", ".txt"):
        raise HTTPException(400, "SOP must be a Markdown (.md) file")
    p = store.create(title.strip() or "SOP video")
    orig = store.originals(p.id)
    names = {}
    for f, role in ((physical, "physical"), (system, "system"), (sop, "sop")):
        ext = Path(f.filename).suffix.lower()
        dst = orig / f"{role}{ext}"
        with dst.open("wb") as out:
            while chunk := await f.read(1 << 20):
                out.write(chunk)
        names[role] = (dst.name, f.filename)
    p.physical = SourceMedia(role="physical", filename=names["physical"][0])
    p.system = SourceMedia(role="system", filename=names["system"][0])
    p.sop = SopDoc(filename=names["sop"][0])
    p.review.auto_approve = auto_approve
    _record(p, "upload", files={r: n[1] for r, n in names.items()})
    if auto_start:
        runner.start(p.id)
    return _view(p)


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return _view(_load(pid))


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    _editable(pid)
    store.delete(pid)
    return {"ok": True}


class RunBody(BaseModel):
    from_stage: str | None = None
    until: str | None = None


@app.post("/api/projects/{pid}/run")
def run_project(pid: str, body: RunBody | None = None):
    _load(pid)
    body = body or RunBody()
    names = [s.name for s in stages()]
    for n in (body.from_stage, body.until):
        if n and n not in names:
            raise HTTPException(400, f"unknown stage {n}")
    if not runner.start(pid, from_stage=body.from_stage, until=body.until):
        raise HTTPException(409, "already running")
    return {"ok": True}


# --------------------------------------------------------------------------- review edits


class SyncBody(BaseModel):
    lag_ms: int | None


@app.put("/api/projects/{pid}/sync")
def set_sync(pid: str, body: SyncBody):
    p = _editable(pid)
    p.sync_override_ms = body.lag_ms
    _record(p, "sync-override", lag_ms=body.lag_ms)
    return _view(p)


class CutBody(BaseModel):
    start_ms: int
    end_ms: int
    reason: str = ""


class TimelineBody(BaseModel):
    intro_ms: int | None = None
    cuts: list[CutBody] | None = None


@app.put("/api/projects/{pid}/timeline")
def set_timeline(pid: str, body: TimelineBody):
    p = _editable(pid)
    if body.intro_ms is not None:
        p.timeline.intro_ms = max(0, min(15_000, body.intro_ms))
    if body.cuts is not None:
        p.timeline.cuts = [Cut(id=f"cut-{i+1}", start_ms=c.start_ms, end_ms=c.end_ms, reason=c.reason)
                           for i, c in enumerate(sorted(body.cuts, key=lambda c: c.start_ms)) if c.end_ms > c.start_ms]
    return _edited(p, "timeline-edit", intro_ms=p.timeline.intro_ms, cuts=[c.model_dump() for c in p.timeline.cuts])


class EventBody(BaseModel):
    start_ms: int | None = None
    end_ms: int | None = None
    view: View | None = None
    action: str | None = None
    kind: Literal["action", "result"] | None = None
    visible_text: list[str] | None = None
    sop_step_id: str | None = None
    narration: str | None = None  # "" resets to the automatic line
    rejected: bool | None = None


def _clamp_times(p: Project, e: Event) -> None:
    dur = p.sync.shared_duration_ms if p.sync else max(e.end_ms, 1)
    e.start_ms = max(0, min(e.start_ms, dur - 100))
    e.end_ms = max(e.start_ms + 100, min(e.end_ms, dur))


@app.post("/api/projects/{pid}/events")
def add_event(pid: str, body: EventBody):
    p = _editable(pid)
    if body.start_ms is None:
        raise HTTPException(400, "start_ms is required")
    end = body.end_ms if body.end_ms is not None else body.start_ms + 2000
    action = (body.action or "").strip() or "New action"
    e = Event(id=event_id(body.start_ms, end, body.view or "both", action), start_ms=body.start_ms, end_ms=end,
              view=body.view or "both", action=action, kind=body.kind or "action",
              visible_text=body.visible_text or [], confidence=1.0, source="human",
              sop_step_id=body.sop_step_id, narration=body.narration or None, human_edited=True)
    _clamp_times(p, e)
    p.events.append(e)
    p.events.sort(key=lambda x: x.start_ms)
    return {**_edited(p, "event-add", event=e.model_dump()), "created": e.id}


@app.patch("/api/projects/{pid}/events/{eid}")
def edit_event(pid: str, eid: str, body: EventBody):
    p = _editable(pid)
    e = p.event(eid)
    if e is None:
        raise HTTPException(404, "event not found")
    changes = body.model_dump(exclude_unset=True)
    if "narration" in changes:
        changes["narration"] = (changes["narration"] or "").strip() or None
    if "action" in changes:
        changes["action"] = (changes["action"] or "").strip() or e.action
    for k, v in changes.items():
        setattr(e, k, v)
    if set(changes) - {"rejected"}:
        e.human_edited = True  # any correction makes it reviewer-placed evidence
    _clamp_times(p, e)
    return _edited(p, "event-edit", event_id=eid, changes=changes)


@app.delete("/api/projects/{pid}/events/{eid}")
def remove_event(pid: str, eid: str):
    """Soft delete: the action no longer counts and is not narrated. Restorable."""
    p = _editable(pid)
    e = p.event(eid)
    if e is None:
        raise HTTPException(404, "event not found")
    e.rejected = True
    return _edited(p, "event-remove", event_id=eid)


@app.post("/api/projects/{pid}/events/{eid}/restore")
def restore_event(pid: str, eid: str):
    p = _editable(pid)
    e = p.event(eid)
    if e is None:
        raise HTTPException(404, "event not found")
    e.rejected = False
    return _edited(p, "event-restore", event_id=eid)


class CoverageBody(BaseModel):
    human_override: EvidenceStatus | None


@app.patch("/api/projects/{pid}/coverage/{step_id}")
def edit_coverage(pid: str, step_id: str, body: CoverageBody):
    p = _editable(pid)
    row = next((c for c in p.coverage.steps if c.step_id == step_id), None)
    if row is None:
        raise HTTPException(404, "step not found")
    row.human_override = body.human_override
    return _edited(p, "coverage-override", step_id=step_id, status=body.human_override)


class CueBody(BaseModel):
    text: str | None = None


@app.patch("/api/projects/{pid}/cues/{cid}")
def edit_cue(pid: str, cid: str, body: CueBody):
    """Compatibility: narration now lives on the event (PATCH /events/{id} {narration})."""
    p = _editable(pid)
    cue = next((c for c in p.cues if c.id == cid), None)
    e = p.event(cue.event_id) if cue else None
    if e is None:
        raise HTTPException(404, "cue not found")
    if body.text is not None:
        e.narration = body.text.strip() or None
        e.human_edited = True
    return _edited(p, "narration-edit", event_id=e.id, text=body.text)


@app.post("/api/projects/{pid}/approve")
def approve(pid: str):
    """The one review stop: render the final video from the current edits."""
    p = _editable(pid)
    with _refresh_guard:
        if t := _refresh_timers.pop(pid, None):
            t.cancel()
    p.review.approved = True
    _record(p, "render-final")
    runner.start(pid)
    return _view(p)


# --------------------------------------------------------------------------- files


@app.get("/api/projects/{pid}/files/{path:path}")
def get_file(pid: str, path: str):
    base = store.dir(pid).resolve()
    target = (base / path).resolve()
    if not str(target).startswith(str(base)) or not path.startswith(("work/", "out/")) or not target.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(target)


@app.get("/api/projects/{pid}/download")
def download(pid: str):
    _load(pid)
    bundle = next(store.out(pid).glob("*_deliverables.zip"), None)
    if bundle is None:
        raise HTTPException(404, "deliverables not packaged yet")
    return FileResponse(bundle, filename=bundle.name)


@app.exception_handler(KeyError)
def _key_error(_req, _exc):
    return JSONResponse({"detail": "not found"}, status_code=404)


_dist = REPO_ROOT / "web" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="web")
