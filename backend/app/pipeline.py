"""Stage runner.

Each stage is an independently retryable unit with a declared set of inputs. A stage reruns only
when the hash of its inputs (upstream outputs + human edits + relevant settings + stage version)
changes, so correcting one narration line regenerates that line's audio, captions, render and QA —
nothing upstream.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import Project, StageStatus, now_iso
from .store import Store, stable_hash

log = logging.getLogger("pipeline")


class Skip(Exception):
    """Stage cannot run in this environment (optional dependency missing, no audio, ...)."""


class AwaitReview(Exception):
    """Human gate: pipeline pauses until the reviewer approves."""


@dataclass
class Ctx:
    project: Project
    store: Store

    @property
    def pid(self) -> str:
        return self.project.id

    @property
    def originals(self) -> Path:
        return self.store.originals(self.pid)

    @property
    def work(self) -> Path:
        return self.store.work(self.pid)

    @property
    def out(self) -> Path:
        return self.store.out(self.pid)

    def physical_path(self) -> Path:
        return self.originals / self.project.physical.filename

    def system_path(self) -> Path:
        return self.originals / self.project.system.filename

    def proxy(self, role: str) -> Path:
        media = self.project.physical if role == "physical" else self.project.system
        return self.work / media.proxy if media and media.proxy else (
            self.physical_path() if role == "physical" else self.system_path()
        )

    def save(self) -> None:
        self.store.save(self.project)

    def provenance(self, stage: str, **info) -> None:
        self.project.provenance[stage] = info


@dataclass
class Stage:
    name: str
    title: str
    version: str
    inputs: Callable[[Project], object]
    run: Callable[[Ctx], str | None]
    gate: bool = False


def _registry() -> list[Stage]:
    from .stages import (
        analyze, captions, gates, narrate, normalize, ocr, package, preview, qa, render, sop, sync,
        timeline, transcribe, tts, validate, windows,
    )

    return [
        validate.STAGE, sop.PARSE, normalize.STAGE, sync.STAGE, timeline.STAGE, preview.STAGE,
        transcribe.STAGE, windows.STAGE, analyze.STAGE, ocr.STAGE, sop.COMPARE, narrate.STAGE,
        tts.STAGE, captions.STAGE, gates.REVIEW, render.FINAL, qa.STAGE, package.STAGE,
    ]


_STAGES: list[Stage] | None = None


def stages() -> list[Stage]:
    global _STAGES
    if _STAGES is None:
        _STAGES = _registry()
    return _STAGES


def stage_names() -> list[str]:
    return [s.name for s in stages()]


def input_hash(stage: Stage, project: Project) -> str:
    return stable_hash({"v": stage.version, "in": stage.inputs(project)})


def effective_states(project: Project) -> dict[str, StageStatus]:
    """Stored statuses, with finished stages whose inputs changed reported as 'stale'."""
    out: dict[str, StageStatus] = {}
    for stage in stages():
        st = project.stages.get(stage.name, StageStatus()).model_copy()
        if st.state in ("done", "skipped") and st.input_hash:
            try:
                if input_hash(stage, project) != st.input_hash:
                    st.state = "stale"
            except Exception:  # noqa: BLE001 - upstream data missing
                st.state = "stale"
        out[stage.name] = st
    return out


class Runner:
    """One background run per project at a time."""

    def __init__(self, store: Store):
        self.store = store
        self._threads: dict[str, threading.Thread] = {}
        self._guard = threading.Lock()

    def is_running(self, pid: str) -> bool:
        t = self._threads.get(pid)
        return bool(t and t.is_alive())

    def wait(self, pid: str, timeout: float) -> bool:
        """Block until the current run finishes (True) or the timeout passes (False)."""
        t = self._threads.get(pid)
        if t and t.is_alive():
            t.join(timeout)
        return not self.is_running(pid)

    def start(self, pid: str, *, from_stage: str | None = None, until: str | None = None) -> bool:
        with self._guard:
            if self.is_running(pid):
                return False
            t = threading.Thread(target=self.run, args=(pid,), kwargs={"from_stage": from_stage, "until": until},
                                 daemon=True, name=f"pipeline-{pid}")
            self._threads[pid] = t
            t.start()
            return True

    def run(self, pid: str, *, from_stage: str | None = None, until: str | None = None) -> Project:
        names = stage_names()
        force_from = names.index(from_stage) if from_stage else len(names)
        stop_at = names.index(until) if until else len(names) - 1
        project = self.store.load(pid)
        ctx = Ctx(project, self.store)

        for i, stage in enumerate(stages()):
            if i > stop_at:
                break
            status = project.stages.setdefault(stage.name, StageStatus())
            try:
                h = input_hash(stage, project)
            except Exception as exc:  # noqa: BLE001
                status.state, status.message = "failed", f"inputs unavailable: {exc}"
                self.store.save(project)
                break
            fresh = status.state in ("done", "skipped") and status.input_hash == h
            if fresh and i < force_from:
                continue

            status.state, status.started_at, status.message = "running", now_iso(), ""
            self.store.save(project)
            t0 = time.monotonic()
            try:
                msg = stage.run(ctx)
                status.state, status.message = "done", msg or ""
            except Skip as exc:
                status.state, status.message = "skipped", str(exc)
            except AwaitReview as exc:
                status.state, status.message = "awaiting_review", str(exc)
                status.input_hash = ""
                status.finished_at = now_iso()
                self.store.save(project)
                break
            except Exception as exc:  # noqa: BLE001
                log.exception("stage %s failed", stage.name)
                status.state = "failed"
                status.message = f"{type(exc).__name__}: {exc}"
                status.finished_at = now_iso()
                project.provenance.setdefault("errors", {})[stage.name] = traceback.format_exc()[-4000:]
                self.store.save(project)
                break
            # Hash after running: stages may legitimately update their own inputs (e.g. merge edits).
            status.input_hash = input_hash(stage, project)
            status.finished_at = now_iso()
            status.duration_s = round(time.monotonic() - t0, 2)
            self.store.save(project)
        return project
