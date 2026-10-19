"""Fast unit tests: timeline remapping, scheduling rules, SOP parsing/comparison, captions, narration."""

from app.captions_fmt import build_captions, parse_srt, to_srt, to_vtt
from app.models import Cut, Event, NarrationCue, OcrHit, Project, SopDoc, Timeline
from app.narration import exact_controls, imperative, shorten
from app.scheduler import GAP_MS, overlaps, schedule
from app.sopparse import parse_sop
from app.stages.sop import align, run_compare
from app.stages.timeline import build_segments
from tests.synth import EVENTS, SOP_MD


def timeline(intro=3000, cuts=(), dur=22000) -> Timeline:
    t = Timeline(intro_ms=intro, cuts=[Cut(id=f"c{i}", start_ms=s, end_ms=e) for i, (s, e) in enumerate(cuts)])
    build_segments(t, dur)
    return t


def test_timeline_intro_and_cuts_remap_every_timestamp():
    t = timeline(cuts=[(5000, 7000), (6500, 8000)])  # overlapping cuts merge
    assert [s.kind for s in t.segments] == ["intro", "source", "source"]
    assert t.duration_ms == 3000 + 22000 - 3000
    assert t.to_output(0) == 3000
    assert t.to_output(4999) == 7999
    assert t.to_output(6000) is None  # cut
    assert t.to_output(8000) == 8000
    assert t.to_shared(8000) == 8000 and t.to_shared(7999) == 4999
    assert t.to_shared(100) is None  # intro


def ev(id, s, e, kind="action", view="system", **kw):
    return Event(id=id, start_ms=s, end_ms=e, view=view, action=f"do {id}", kind=kind, **kw)


def cue(id, event_id, dur, kind="action"):
    return NarrationCue(id=id, event_id=event_id, text="Line.", kind=kind, duration_ms=dur)


def test_scheduler_never_overlaps_and_results_wait_for_evidence():
    t = timeline(intro=0)
    events = {e.id: e for e in [ev("a", 1000, 1500), ev("b", 1600, 2000), ev("r", 2100, 4000, kind="result")]}
    cues = [cue("ca", "a", 1400), cue("cb", "b", 900), cue("cr", "r", 800, kind="result")]
    placed = {p.cue_id: p for p in schedule(cues, events, t, finalize=True)}
    assert not overlaps(list(placed.values()))
    assert placed["cb"].start >= placed["ca"].end + GAP_MS
    assert placed["cr"].start >= t.to_output(2100)  # never before the result is visible
    assert placed["ca"].start <= t.to_output(1000)  # action line leads its action
    assert placed["cb"].overrun_ms > 0 and placed["cb"].notes  # too long for its window -> reported for shortening


def test_scheduler_drops_cues_in_cut_ranges_and_never_crosses_cuts():
    t = timeline(intro=2000, cuts=[(3000, 6000)])
    events = {e.id: e for e in [ev("cut", 4000, 5000), ev("edge", 2500, 2900)]}
    placed = {p.cue_id: p for p in schedule([cue("c1", "cut", 500), cue("c2", "edge", 2000)], events, t, finalize=True)}
    assert placed["c1"].dropped  # event was cut
    assert placed["c2"].dropped and any("cut" in n for n in placed["c2"].notes)  # would cross the cut


def test_sop_parse_stable_ids_controls_and_evidence():
    title, steps = parse_sop(SOP_MD)
    assert title == "Receive Pallet into Location"
    assert [s.id for s in steps] == ["step_01", "step_02", "step_03", "step_04", "step_05"]
    assert steps[0].controls == ["Receiving"]
    assert steps[2].controls == ["Confirm", "Received"]
    assert steps[2].completion_evidence
    assert steps[3].expected_view == "physical"
    assert steps[0].prerequisites == ["Handheld scanner is charged"]
    assert steps[4].warnings == ["never stack pallets above the red line."]


def _project_with_events() -> Project:
    p = Project(id="t", title="t", sop=SopDoc(filename="sop.md"))
    _, p.sop_steps = parse_sop(SOP_MD)
    p.events = [Event(id=f"ev-{i}", source="vlm-coarse", **e) for i, e in enumerate(EVENTS)]
    return p


class _Ctx:
    def __init__(self, p):
        self.project = p


def test_compare_marks_coverage_and_flags_differences():
    p = _project_with_events()
    run_compare(_Ctx(p))
    status = {c.step_id: c.status for c in p.coverage.steps}
    assert status["step_01"] == "demonstrated"
    assert status["step_03"] == "demonstrated"  # Confirm tapped + Received result visible
    assert status["step_04"] == "demonstrated"
    assert status["step_05"] == "missing"  # Put Away never shown — not invented
    kinds = {d.kind for d in p.coverage.discrepancies}
    assert "not-in-sop" in kinds  # pallet wrapping is not in the SOP
    assert p.event("ev-5").sop_step_id is None


def test_alignment_preserves_sop_order_and_human_assignment():
    p = _project_with_events()
    p.events[4].human_edited = True
    p.events[4].sop_step_id = "step_05"
    out = align(p.live_events(), p.sop_steps)
    assert out["ev-4"] == "step_05"
    assert out["ev-0"] == "step_01"


def test_speech_is_context_not_proof():
    from app.models import TranscriptSegment

    p = _project_with_events()
    p.transcript = [TranscriptSegment(role="physical", start_ms=20000, end_ms=21000,
                                      text="now tap put away to finish")]
    run_compare(_Ctx(p))
    row = next(c for c in p.coverage.steps if c.step_id == "step_05")
    assert row.status == "unclear" and "speech" in row.notes[0]
    assert any(d.kind == "speech-only" for d in p.coverage.discrepancies)


def test_captions_follow_measured_schedule_exactly():
    cues = [NarrationCue(id="c1", event_id="e", text="Tap Confirm and wait until the Received message appears on the WISE screen.",
                         out_start_ms=1000, out_end_ms=4600),
            NarrationCue(id="c2", event_id="e", text="Done.", out_start_ms=5000, out_end_ms=5600, dropped=True)]
    caps = build_captions(cues)
    assert caps[0].start_ms == 1000 and caps[-1].end_ms == 4600
    assert all(len(line) <= 42 for c in caps for line in c.text.splitlines())
    assert {c.cue_id for c in caps} == {"c1"}
    srt = parse_srt(to_srt(caps))
    assert [(s, e) for s, e, _ in srt] == [(c.start_ms, c.end_ms) for c in caps]
    assert to_vtt(caps).startswith("WEBVTT")


def test_narration_wording_rules():
    assert imperative("The worker taps Confirm") == "Tap Confirm."
    assert imperative("Operator scans the pallet label") == "Scan the pallet label."
    assert exact_controls("Tap confirm.", ["CONFIRM"]) == "Tap CONFIRM."
    long = "Carefully tap the Confirm button on the screen, and then wait for the green Received message to appear"
    short = shorten(long)
    assert len(short.split()) < len(long.split()) and short.endswith(".")


class _NarrCtx(_Ctx):
    pass


def _placeholder(id, s, e, **kw):
    return Event(id=id, start_ms=s, end_ms=e, view="system", action="Screen change on the WISE system",
                 confidence=0.2, source="mock", review_required=True, **kw)


def test_placeholders_are_hints_until_a_person_touches_them():
    from app.stages.narrate import run as narrate

    p = _project_with_events()
    p.events = [_placeholder("ev-p1", 1000, 2000), _placeholder("ev-p2", 3000, 4000)]
    run_compare(_NarrCtx(p))
    narrate(_NarrCtx(p))
    assert p.cues == []  # nothing to approve, nothing narrated
    assert all(c.status == "missing" for c in p.coverage.steps)  # hints are never evidence

    # dragging a hint to new times does not make it narrated evidence
    p.events[1].start_ms, p.events[1].human_edited = 3200, True
    run_compare(_NarrCtx(p))
    narrate(_NarrCtx(p))
    assert p.cues == [] and p.events[1].placeholder

    # reviewer only assigns a step -> it counts and narration defaults to the step title
    p.events[0].sop_step_id, p.events[0].human_edited = "step_01", True
    run_compare(_NarrCtx(p))
    narrate(_NarrCtx(p))
    assert next(c for c in p.coverage.steps if c.step_id == "step_01").status == "demonstrated"
    assert [c.text for c in p.cues] == ["Open the Receiving menu in WISE."]

    # reviewer writes the narration -> used verbatim; removing the action removes its line
    p.events[0].narration = "Open Receiving"
    narrate(_NarrCtx(p))
    assert p.cues[0].text == "Open Receiving." and p.cues[0].human_edited
    p.events[0].rejected = True
    narrate(_NarrCtx(p))
    assert p.cues == []


def test_review_gate_waits_only_for_render_click():
    import pytest as _pt

    from app.pipeline import AwaitReview
    from app.stages.gates import run as gate

    p = _project_with_events()
    with _pt.raises(AwaitReview):
        gate(_NarrCtx(p))
    p.review.approved = True
    assert gate(_NarrCtx(p)) == "approved"


def test_bold_step_titles_are_not_controls():
    _, steps = parse_sop("# T\n\n1. **Confirm the request**\n   Tap **Start** to begin.\n2. Plain step.\n")
    assert steps[0].title == "Confirm the request"
    assert steps[0].controls == ["Start"]
    assert steps[1].title == "Plain step"


def test_ocr_text_matching():
    from app.stages.ocr import text_present

    assert text_present("Put Away", ["PUT AWAY", "Cancel"])
    assert not text_present("Confirm", ["Cancel"])
    e = Event(id="x", start_ms=0, end_ms=1, view="system", action="a", ocr=[OcrHit(text="Confirm", score=0.9)])
    assert e.ocr[0].text == "Confirm"


def test_edge_voice_is_selected_and_validated(monkeypatch):
    from dataclasses import replace
    import pytest
    from app.providers import tts
    monkeypatch.setattr(tts, "settings", replace(tts.settings, tts_provider="edge"))
    voice = tts.get_tts()
    assert isinstance(voice, tts.EdgeTts) and voice.voice_id() == "edge:en-US-JennyNeural:+0%:+0Hz:+0%"
    monkeypatch.setattr(tts, "settings", replace(tts.settings, tts_edge_rate="fast"))
    with pytest.raises(ValueError, match="RATE"):
        tts.EdgeTts()


def test_old_project_file_loads_as_legacy_template():
    p = Project.model_validate({"id": "old", "title": "Old", "timeline": {"intro_ms": 3000,
                                "segments": [{"kind": "intro", "out_start_ms": 0, "out_end_ms": 3000}]}})
    assert p.template == "legacy" and p.timeline.title_ms == 0 and p.timeline.outro_ms == 0


def test_item_template_timeline_has_bookends_title_and_frame_grid():
    tl = Timeline(intro_ms=5067, title_ms=4000, outro_ms=4200, cuts=[Cut(id="c1", start_ms=2000, end_ms=3000)])
    build_segments(tl, 10_000)
    assert [s.kind for s in tl.segments] == ["intro", "title", "source", "source", "outro"]
    intro, title, outro = tl.segments[0], tl.segments[1], tl.segments[-1]
    assert (intro.from_frame, intro.duration_frames) == (0, 152)
    assert (title.from_frame, title.duration_frames) == (152, 120)
    assert (outro.from_frame, outro.duration_frames) == (542, 126)  # 18067 ms -> frame 542
    assert tl.to_output(10_000) == tl.segments[-2].out_end_ms  # last footage instant, not the outro
    assert tl.duration_ms == 5067 + 4000 + 9000 + 4200


def test_remotion_progress_lines_are_parsed():
    from app.renderer import parse_progress
    assert parse_progress("Rendering frames    ━━━  1234/5286 2m 10s remaining") == ("rendering", 1234, 5286, "2m 10s")
    assert parse_progress("\x1b[90mEncoding video\x1b[39m ━━ 100/5286") == ("encoding", 100, 5286, "")
    assert parse_progress("Bundling 50%") is None


def test_only_one_video_renders_at_a_time(tmp_path):
    import threading
    from app import pipeline
    from app.pipeline import Ctx, Runner, Stage
    from app.store import Store
    store, active, peak, gate = Store(tmp_path), [0], [0], threading.Event()

    def heavy(ctx):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        gate.wait(5)
        active[0] -= 1
        return "rendered"

    stage = Stage("render", "Final render", "t", lambda p: {"id": p.id}, heavy)
    original = pipeline.stages
    pipeline.stages = lambda: [stage]
    try:
        runner = Runner(store)
        ids = [store.create(f"video {i}").id for i in range(2)]
        for pid in ids:
            runner.start(pid)
        threading.Timer(0.5, gate.set).start()
        assert all(runner.wait(pid, 10) for pid in ids)
    finally:
        pipeline.stages = original
    assert peak[0] == 1
    assert all(store.load(pid).stages["render"].state == "done" for pid in ids)


def test_shorten_only_removes_filler_never_facts():
    from app.narration import shorten
    line = "Scan the LP label with the handheld camera to add the LP to the list."
    assert shorten(line) == line
    assert shorten("Then simply tap Confirm on the screen.") == "Then tap Confirm."
    keep = "Do not move the pallet until the quantity is confirmed, then close the task."
    assert shorten(keep) == keep


def test_sop_introduction_is_parsed():
    from app.sopparse import parse_intro
    assert parse_intro("# Move\n\n**Purpose.** Move inventory safely.\n\n## 1. Open") == "Move inventory safely."
    assert parse_intro("# Move\n\n## Purpose\nMove it.\nKeep records.\n\n## 1. Open") == "Move it. Keep records."
    assert parse_intro("# Move\n\n1. Open the app") == ""


def test_title_card_grows_to_fit_the_spoken_introduction():
    from app.stages.title_voice import card_ms
    assert card_ms(1000) == 4000                     # short intro: keep the 4 s minimum
    long = card_ms(12_000)                           # 0.5 s lead + 12 s voice + 0.7 s tail
    assert long >= 13_200 and abs(long * 30 / 1000 - round(long * 30 / 1000)) < 0.05  # on the frame grid


def test_intro_line_reads_title_then_purpose():
    from app.models import Project, SopDoc
    from app.stages.narrate import intro_line
    p = Project(id="p", title="x", sop=SopDoc(filename="s.md", title="Inventory Movement", purpose="Move stock safely"))
    assert intro_line(p) == "Inventory Movement. Move stock safely."


def test_speech_context_is_english_with_language_and_time():
    from app.models import Project, TranscriptSegment
    from app.stages.analyze import speech_context
    p = Project(id="p", title="x", transcript=[
        TranscriptSegment(role="physical", start_ms=1000, end_ms=3000, text="escanear la orden", language="es",
                          text_en="scan the order"),
        TranscriptSegment(role="system", start_ms=9000, end_ms=9500, text="tap confirm", language="en", text_en="tap confirm")])
    ctx = speech_context(p, 0, 5000)
    assert ctx == '[1.0s floor mic, es->en] "scan the order"'


def test_script_uses_nearby_speech_and_falls_back_to_rules(monkeypatch):
    from app.models import Event, NarrationCue, Project, SopStep, TranscriptSegment
    from app.stages import narrate
    p = Project(id="p", title="x",
                events=[Event(id="ev-1", start_ms=5000, end_ms=6000, view="system", action="Taps Close Task",
                              visible_text=["CLOSE TASK"], sop_step_id="s1", confidence=0.9)],
                sop_steps=[SopStep(id="s1", number="1", title="Close the Task", text="Close the Task")],
                transcript=[TranscriptSegment(role="physical", start_ms=4000, end_ms=5500, text="y cierran la tarea",
                                              language="es", text_en="and they close the task")])
    seen = {}

    class FakeLlm:
        def structured(self, messages, schema, **kw):
            seen["prompt"] = messages[1]["content"]
            return schema(lines=[{"id": "cue-1", "line": "Tap CLOSE TASK to finish the movement"}])

    cues = [NarrationCue(id="cue-1", event_id="ev-1", text="Tap Close Task.", sop_step_id="s1")]
    assert narrate.write_script(FakeLlm(), p, cues) == {"cue-1": "Tap CLOSE TASK to finish the movement."}
    assert "and they close the task" in seen["prompt"] and "es->en" in seen["prompt"]


def test_transcript_translation_sets_language_and_english(monkeypatch, tmp_path):
    from app.models import TranscriptSegment, TranscriptWord
    from app.pipeline import Ctx
    from app.providers import llm as llm_mod
    from app.stages import transcribe
    from app.store import Store

    class FakeLlm:
        def structured(self, messages, schema, **kw):
            return schema(items=[{"id": 0, "language": "es", "english": "print the label"},
                                 {"id": 1, "language": "en", "english": "scan the LP"}])

    monkeypatch.setattr(llm_mod, "get_llm", lambda: FakeLlm())
    store = Store(tmp_path)
    p = store.create("t")
    segs = [TranscriptSegment(role="physical", start_ms=0, end_ms=900, text="printear el leivo"),
            TranscriptSegment(role="system", start_ms=1000, end_ms=1900, text="scan the LP")]
    transcribe._translate(Ctx(p, store), segs)
    assert [(s.language, s.text_en) for s in segs] == [("es", "print the label"), ("en", "scan the LP")]
    # long Whisper segments are split at word timings
    words = [TranscriptWord(role="physical", start_ms=i * 1000, end_ms=i * 1000 + 800, text=f"w{i}.") for i in range(30)]
    long = TranscriptSegment(role="physical", start_ms=0, end_ms=29_800, text="...", words=words)
    parts = transcribe._split(long)
    assert len(parts) > 1 and all(p.end_ms - p.start_ms <= transcribe.MAX_SEGMENT_MS for p in parts)


def test_save_retries_when_windows_briefly_locks_the_file(monkeypatch, tmp_path):
    from pathlib import Path as P
    from app.store import Store
    store = Store(tmp_path)
    p = store.create("locked")
    real, calls = P.replace, []

    def flaky(self, target):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(5, "Access is denied")
        return real(self, target)

    monkeypatch.setattr(P, "replace", flaky)
    p.title = "saved anyway"
    store.save(p)
    assert len(calls) == 3 and store.load(p.id).title == "saved anyway"
