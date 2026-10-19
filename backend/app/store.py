"""On-disk project store: projects/<id>/{originals,work,out,project.json}.

Originals are written once and never modified; every derived file lives in work/ or out/.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from .config import settings
from .models import Project, now_iso

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_hash(obj) -> str:
    blob = json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "project"


class Store:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or settings.data_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    # paths -----------------------------------------------------------------
    def dir(self, pid: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]+", pid):
            raise KeyError(pid)
        return self.root / pid

    def originals(self, pid: str) -> Path:
        return self.dir(pid) / "originals"

    def work(self, pid: str) -> Path:
        return self.dir(pid) / "work"

    def out(self, pid: str) -> Path:
        return self.dir(pid) / "out"

    def lock(self, pid: str) -> threading.RLock:
        with _locks_guard:
            return _locks.setdefault(pid, threading.RLock())

    # crud ------------------------------------------------------------------
    def create(self, title: str) -> Project:
        pid = f"{slug(title)}-{uuid.uuid4().hex[:6]}"
        for sub in ("originals", "work", "out"):
            (self.dir(pid) / sub).mkdir(parents=True, exist_ok=True)
        project = Project(id=pid, title=title, template="item-v1")
        from .brand import bookend_counts
        counts = bookend_counts()  # natural frame counts of the official bookends at 30 fps
        project.timeline.intro_ms = round(counts["intro"] * 1000 / 30)
        project.timeline.title_ms = 4000
        project.timeline.outro_ms = round(counts["outro"] * 1000 / 30)
        self.save(project)
        return project

    def load(self, pid: str) -> Project:
        path = self.dir(pid) / "project.json"
        if not path.exists():
            raise KeyError(pid)
        return Project.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, project: Project) -> None:
        with self.lock(project.id):
            project.updated_at = now_iso()
            path = self.dir(project.id) / "project.json"
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(project.model_dump_json(indent=2), encoding="utf-8")
            # Windows refuses to replace a file another reader (the web page polling, a virus scanner) has
            # open at that instant. Those reads take milliseconds, so retry briefly instead of failing a stage.
            for attempt in range(20):
                try:
                    tmp.replace(path)
                    break
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.05 * (attempt + 1))

    def list(self) -> list[Project]:
        out = []
        for p in sorted(self.root.glob("*/project.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                out.append(Project.model_validate_json(p.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001 - a broken project should not hide the others
                continue
        return out

    def delete(self, pid: str) -> None:
        shutil.rmtree(self.dir(pid), ignore_errors=True)
