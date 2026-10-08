import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

# Settings are read at import time: configure a hermetic, GPU-free environment first.
_TMP = Path(tempfile.mkdtemp(prefix="sopvb-tests-"))
os.environ.setdefault("SOP_DATA_DIR", str(_TMP / "projects"))
os.environ.setdefault("VLM_PROVIDER", "mock")
os.environ.setdefault("LLM_PROVIDER", "none")
os.environ.setdefault("TTS_PROVIDER", "mock")
os.environ.setdefault("RENDERER", os.environ.get("TEST_RENDERER", "ffmpeg"))
os.environ["SKELLYSYNC_PYTHON"] = os.environ.get("TEST_SKELLYSYNC_PYTHON", str(_TMP / "no-skellysync"))
_events = _TMP / "mock_events.json"
os.environ.setdefault("MOCK_EVENTS_PATH", str(_events))

from tests import synth  # noqa: E402  (imports app.config, which reads the env above)

_events.write_text(json.dumps(synth.EVENTS), encoding="utf-8")


@pytest.fixture(scope="session")
def media() -> tuple[Path, Path, Path]:
    return synth.make_pair(_TMP / "media")


@pytest.fixture(scope="session")
def store():
    from app.store import Store

    return Store()


def new_project(store, media, *, auto_approve=True, title="Receive pallet"):
    from app.models import SopDoc, SourceMedia

    physical, system, sop = media
    p = store.create(title)
    for src, name in ((physical, "physical.mp4"), (system, "system.mp4"), (sop, "sop.md")):
        shutil.copy2(src, store.originals(p.id) / name)
    p.physical = SourceMedia(role="physical", filename="physical.mp4")
    p.system = SourceMedia(role="system", filename="system.mp4")
    p.sop = SopDoc(filename="sop.md")
    p.review.auto_approve = auto_approve
    store.save(p)
    return p


def pytest_sessionfinish(session, exitstatus):
    if exitstatus == 0 and not os.environ.get("KEEP_TEST_DATA"):
        shutil.rmtree(_TMP, ignore_errors=True)
