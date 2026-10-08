"""Media tests on the synthetic pair: sync accuracy, full mock pipeline, partial reruns."""

import csv

import pytest

from app.pipeline import Runner
from tests.conftest import new_project
from tests.synth import LAG_MS, SYSTEM_MS

pytestmark = pytest.mark.slow


def test_sync_recovers_known_offset_and_verifies_three_windows(store, media):
    p = new_project(store, media, title="sync check")
    p = Runner(store).run(p.id, until="timeline")
    assert p.stages["sync"].state == "done", p.stages["sync"].message
    assert abs(p.sync.lag_ms - LAG_MS) <= 40, p.sync
    assert [c.label for c in p.sync.checks] == ["start", "middle", "end"]
    assert all(c.ok for c in p.sync.checks), p.sync.checks
    assert p.sync.drift_ms <= 40
    assert abs(p.sync.shared_duration_ms - SYSTEM_MS) <= 80
    assert not p.sync.needs_review, p.sync.review_reasons
    # extra floor footage before the WISE recording starts is flagged, not silently dropped
    assert any(o.role == "physical" for o in p.sync.outside) or p.sync.shared_start_physical_ms < 1600


def test_manual_offset_wins(store, media):
    p = new_project(store, media, title="manual sync")
    p.sync_override_ms = 1520
    store.save(p)
    p = Runner(store).run(p.id, until="sync")
    assert p.sync.lag_ms == 1520 and p.sync.manual and p.sync.method == "manual"


def test_end_to_end_mock_pipeline_and_partial_rerun(store, media):
    p = new_project(store, media, title="e2e")
    runner = Runner(store)
    p = runner.run(p.id)
    failed = {k: v.message for k, v in p.stages.items() if v.state == "failed"}
    assert not failed, failed
    assert p.stages["package"].state == "done"

    out = store.out(p.id)
    for name in ("final.mp4", "final_720p.mp4", "captions.srt", "captions.vtt", "narration_transcript.txt",
                 "narration_timing.csv", "source_time_map.csv", "sop_coverage_report.md", "qa_report.json",
                 "manifest.json", "project.json", "poster.jpg"):
        assert (out / name).exists(), name
    assert next(out.glob("*_deliverables.zip"))

    qa = p.qa[-1]
    errors = [c for c in qa.checks if not c.ok and c.severity == "error"]
    assert not errors, errors

    # coverage: Put Away (step 5) was never shown and is reported as not demonstrated
    status = {c.step_id: c.status for c in p.coverage.steps}
    assert status["step_05"] == "missing"
    report = (out / "sop_coverage_report.md").read_text(encoding="utf-8")
    assert "Step 5" in report
    assert "not-in-sop" in report  # pallet wrapping was observed but is not an SOP step
    assert not any(c.event_id == p.events[-1].id for c in p.cues)  # ...and is not narrated unless approved

    # narration rows exist for scheduled cues, and nothing overlaps
    rows = list(csv.DictReader((out / "narration_timing.csv").open(encoding="utf-8")))
    live = sorted((r for r in rows if r["dropped"] == "False"), key=lambda r: int(r["start_ms"]))
    assert live
    assert all(int(a["end_ms"]) < int(b["start_ms"]) for a, b in zip(live, live[1:]))

    # --- correction without restarting: edit one narration line (it lives on the event)
    before = {k: v.finished_at for k, v in p.stages.items()}
    ev = p.event(p.cues[0].event_id)
    ev.narration, ev.human_edited = "Open the Receiving menu.", True
    store.save(p)
    p = runner.run(p.id)
    reran = {k for k, v in p.stages.items() if v.finished_at != before.get(k)}
    assert {"validate", "sync", "preview", "analyze"}.isdisjoint(reran), reran
    assert {"narrate", "tts", "captions", "render", "qa", "package"} <= reran, reran
    assert p.cues[0].text == "Open the Receiving menu."
    assert p.cues[0].human_edited

    # --- adding an intro / cut moves every timestamp through the shared timeline
    p.timeline.intro_ms = 5000
    store.save(p)
    p = runner.run(p.id)
    first = min(c.out_start_ms for c in p.cues if not c.dropped)
    assert first >= 5000
    assert p.qa[-1].ok, [c for c in p.qa[-1].checks if not c.ok]
