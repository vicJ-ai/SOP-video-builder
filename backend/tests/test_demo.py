"""Demo video mode: one recording + optional notes, customer-facing narration. SOP mode is untouched."""

import pytest

from app.models import Event, NarrationCue, Project, SopDoc, SourceMedia, SyncResult
from app.pipeline import Ctx, Skip, input_hash, stages
from app.stages.timeline import build_segments
from tests.synth import EVENTS


class _Ctx:
    def __init__(self, p):
        self.project = p

    def system_path(self):
        return "system.mp4"

    def proxy(self, role):
        return f"proxy/{role}.mp4"

    def provenance(self, stage, **info):
        self.project.provenance[stage] = info


def _demo(role="system", *, notes=True) -> Project:
    p = Project(id="d", title="Create an order", template="item-v1", mode="demo")
    setattr(p, role, SourceMedia(role=role, filename=f"{role}.mp4", sha256="abc", duration_ms=22000,
                                 width=1920, height=1080, proxy=f"proxy/{role}.mp4"))
    if notes:
        p.notes = SopDoc(filename="notes.md", sha256="n1", title="Orders in WISE",
                         purpose="Create and release an order in under a minute.")
    p.sync = SyncResult(lag_ms=0, method="single-recording", shared_duration_ms=22000)
    p.timeline.title_ms = 4000
    build_segments(p.timeline, 22000)
    p.events = [Event(id=f"ev-{i}", source="vlm-coarse", **{**e, "view": role}) for i, e in enumerate(EVENTS)]
    return p


def test_old_projects_load_as_sop_videos():
    p = Project.model_validate_json('{"id": "x", "title": "x"}')
    assert p.mode == "sop" and p.notes is None


@pytest.mark.parametrize("role", ["system", "physical"])
def test_every_stage_input_works_with_one_recording(role):
    p = _demo(role)
    p.cues = [NarrationCue(id="cue-0", event_id="ev-0", text="Here you can see the order list.")]
    for stage in stages():
        input_hash(stage, p)  # raises if a stage still assumes two recordings or an SOP


def test_single_recording_skips_sync_but_sets_its_own_clock(tmp_path):
    from app.stages import sync
    from app.store import Store

    store = Store(tmp_path)
    p = store.create("demo")
    p.mode = "demo"
    p.system = SourceMedia(role="system", filename="system.mp4", duration_ms=15000)
    with pytest.raises(Skip, match="nothing to sync"):
        sync.run(Ctx(p, store))
    assert p.sync.lag_ms == 0 and p.sync.shared_duration_ms == 15000 and not p.sync.needs_review
    assert p.sync.physical_ms(1234) == p.sync.system_ms(1234) == 1234


def test_sop_parse_compare_preview_and_windows_are_skipped():
    from app.stages import preview, sop, windows

    p = _demo()
    for run in (sop.run_parse, sop.run_compare, preview.run, windows.run):
        with pytest.raises(Skip):
            run(_Ctx(p))


SPEECH = [  # what the presenter said (Spanish translated), shared ms
    (1000, 3500, "Primero abrimos la lista de órdenes", "First we open the order list"),
    (4000, 4600, "eh", "uh"),
    (9000, 12000, "Ahora le damos a crear y se guarda", "Now we tap create and it saves"),
]


def _spoken_demo(**kw) -> Project:
    from app.models import TranscriptSegment

    p = _demo(**kw)
    p.events = []
    p.transcript = [TranscriptSegment(role="system", start_ms=s, end_ms=e, text=t, language="es", text_en=en)
                    for s, e, t, en in SPEECH]
    return p


class _LookVlm:
    name = "fake"

    def __init__(self):
        self.calls = []

    def available(self):
        return True

    def look(self, frames, *, instruction, schema):
        self.calls.append((len(frames), instruction))
        ids = [l.split()[2] for l in instruction.splitlines() if l.startswith("- id ")]
        return schema(moments=[{"id": ids[0], "seen": "The Orders list opens", "visible_text": ["Orders"]},
                               {"id": ids[1], "seen": "Order 1042 saved", "kind": "result",
                                "visible_text": ["Saved", ""]}])


def test_demo_moments_come_from_what_the_presenter_said(monkeypatch):
    from app.stages import analyze

    vlm = _LookVlm()
    monkeypatch.setattr(analyze, "get_vlm", lambda: vlm)
    monkeypatch.setattr(analyze, "_frames", lambda path, times, label, **kw: [object()] * len(times))
    p = _spoken_demo(notes=False)
    msg = analyze.run(_Ctx(p))
    assert [(e.start_ms, e.end_ms) for e in p.events] == [(1000, 3500), (9000, 12000)]  # "uh" is filler
    assert all(e.source == "speech" and e.view == "system" and e.trusted for e in p.events)
    assert [(e.kind, e.visible_text, e.action) for e in p.events] == [
        ("action", ["Orders"], "The Orders list opens"), ("result", ["Saved"], "Order 1042 saved")]
    assert analyze.spoken(p, p.events[1]) == "Now we tap create and it saves"
    assert len(vlm.calls) == 1 and vlm.calls[0][0] == 2  # one quick look, one frame per moment
    assert "Now we tap create and it saves" in vlm.calls[0][1] and "2 spoken moments, 2 checked" in msg


def test_demo_without_speech_stops_with_a_clear_message(monkeypatch):
    from app.stages import analyze

    monkeypatch.setattr(analyze, "get_vlm", lambda: _LookVlm())
    p = _spoken_demo()
    p.transcript = []
    with pytest.raises(ValueError, match="say each action out loud"):
        analyze.run(_Ctx(p))


def _with_speech_events(p):
    from app.stages import analyze

    p.events = analyze._speech_events(_Ctx(p))
    return p


def test_demo_narration_without_a_model_uses_what_was_said(monkeypatch):
    from app.stages import narrate

    monkeypatch.setattr(narrate, "get_llm", lambda: None)
    p = _with_speech_events(_spoken_demo())
    narrate.run(_Ctx(p))
    title, *lines = p.cues
    assert title.phase == "title" and title.text.startswith("Orders in WISE.")
    assert [c.text for c in lines] == ["First we open the order list.", "Now we tap create and it saves."]
    assert [p.event(c.event_id).start_ms for c in lines] == [1000, 9000]  # Jenny speaks where the presenter spoke
    assert all(c.heading for c in lines)


def test_demo_script_rewrites_speech_for_customers(monkeypatch):
    from app.stages import narrate

    seen = {}

    class FakeLlm:
        def structured(self, messages, schema, **kw):
            seen["system"], seen["user"] = messages[0]["content"], messages[1]["content"]
            first = json.loads(messages[1]["content"])["moments"][0]["id"]
            return schema(lines=[{"id": first, "line": "Here you can see the full order list in one place",
                                  "heading": "All Orders at a Glance"}])

    import json
    monkeypatch.setattr(narrate, "get_llm", lambda: FakeLlm())
    p = _with_speech_events(_spoken_demo(notes=False))
    narrate.run(_Ctx(p))
    lines = [c for c in p.cues if c.phase == "operational"]
    assert [(c.text, c.heading) for c in lines] == [("Here you can see the full order list in one place.",
                                                     "All Orders at a Glance")]  # the model dropped the other moment
    assert "customers" in seen["system"] and "Never number steps" in seen["system"]
    moments = json.loads(seen["user"])["moments"]
    assert moments[0]["said"] == "First we open the order list"
    assert moments[0]["max_words"] == 20  # 8 s until the next moment at 2.6 words/s
    assert moments[1]["max_words"] == 30  # 13 s left, capped at 30 words


def test_demo_intro_uses_notes_or_title():
    from app.stages.narrate import intro_line

    assert intro_line(_demo()) == "Orders in WISE. Create and release an order in under a minute."
    assert intro_line(_demo(notes=False)) == "Create an order. Let's take a look."


def test_demo_render_props_show_one_recording_without_test_labels():
    from app.stages.captions import build_layout
    from app.stages.render import build_props

    p = _demo()
    p.cues = [NarrationCue(id="cue-0", event_id="ev-0", text="x", heading="Start Receiving Fast", out_start_ms=9000,
                           out_end_ms=11000, anchor_out_ms=9000)]
    p.layout = build_layout(p)
    props = build_props(_Ctx(p), draft=False)
    assert props["physical"] is None and props["system"]["label"] == "Screen"
    assert props["variant"] == "demo" and props["demo"] is False  # never "synthetic test source"
    assert props["title"] == "Orders in WISE" and props["subtitle"].startswith("Create and release")
    assert props["workflow"] == ["Start Receiving Fast"]
    assert all(s["availableRoles"] == ["system"] for s in props["segments"])


def test_create_demo_project_api(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    client = TestClient(main.app)
    video = ("screen.mp4", b"not really a video", "video/mp4")
    r = client.post("/api/projects", data={"mode": "demo", "auto_start": "false"},
                    files={"system": video, "notes": ("notes.md", b"# Orders\n\nFast.", "text/markdown")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "demo" and body["physical"] is None and body["system"]["filename"] == "system.mp4"
    assert body["notes"]["filename"] == "notes.md" and body["title"] == "screen"
    assert any(s["mode"] == "demo" for s in client.get("/api/projects").json())
    two = client.post("/api/projects", data={"mode": "demo", "auto_start": "false"},
                      files={"system": video, "physical": video})
    assert two.status_code == 400
    sop_missing = client.post("/api/projects", data={"title": "t", "auto_start": "false"},
                              files={"system": video, "physical": video})
    assert sop_missing.status_code == 400 and "SOP" in sop_missing.json()["detail"]
