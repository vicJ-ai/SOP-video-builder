"""Canonical data model.

The project file (project.json) is the database; the rendered MP4 is only a view of it.

Clocks — all integer milliseconds, never float seconds:
  * physical source time  — position in the physical-work recording
  * system source time    — position in the WISE/system recording
  * shared time           — 0 at the start of the shared (overlapping) interval. Events,
                            transcript words and narration anchors live here.
  * output time           — position in the edited video (after intro / cuts).
Conversions go through ``SyncResult`` (source <-> shared) and ``Timeline`` (shared <-> output).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

View = Literal["system", "physical", "both"]
StageState = Literal["pending", "running", "done", "skipped", "failed", "awaiting_review", "stale"]
EvidenceStatus = Literal["demonstrated", "partial", "missing", "unclear"]
SPEEDUP_RATE = 3  # fast-forwarded pauses play at 3x


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- sources


class SourceMedia(BaseModel):
    role: Literal["physical", "system"]
    filename: str  # name inside originals/
    sha256: str = ""
    duration_ms: int = 0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    vfr: bool = False
    has_audio: bool = False
    audio_sample_rate: int = 0
    video_codec: str = ""
    decode_errors: list[str] = Field(default_factory=list)
    proxy: str = ""  # work/ path of the CFR proxy (same timestamps, same speed)


class SopDoc(BaseModel):
    filename: str
    sha256: str = ""
    title: str = ""
    purpose: str = ""  # the SOP's introduction ("Purpose." paragraph), voiced over the title card


# --------------------------------------------------------------------------- sync


class SyncEstimate(BaseModel):
    method: str  # skellysync-audio | skellysync-brightness | gcc-phat-audio | onset-envelope | brightness-xcorr | manual
    lag_ms: int  # physical_time = system_time + lag_ms
    score: float = 0.0  # method-specific confidence 0..1
    detail: str = ""


class SyncCheck(BaseModel):
    label: Literal["start", "middle", "end"]
    shared_ms: int  # window centre in shared time
    local_lag_ms: int | None = None
    error_ms: int | None = None  # local_lag - chosen lag
    score: float = 0.0
    ok: bool = False


class OutsideFootage(BaseModel):
    """Activity that falls outside the shared interval: flagged, never silently discarded."""

    role: Literal["physical", "system"]
    source_start_ms: int
    source_end_ms: int
    reason: str


class SyncResult(BaseModel):
    lag_ms: int = 0
    method: str = ""
    estimates: list[SyncEstimate] = Field(default_factory=list)
    checks: list[SyncCheck] = Field(default_factory=list)
    drift_ms: int = 0  # spread of local lags across start/middle/end
    shared_start_physical_ms: int = 0
    shared_duration_ms: int = 0
    outside: list[OutsideFootage] = Field(default_factory=list)
    manual: bool = False
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)

    def physical_ms(self, shared_ms: int) -> int:
        return self.shared_start_physical_ms + shared_ms

    def system_ms(self, shared_ms: int) -> int:
        return self.shared_start_physical_ms + shared_ms - self.lag_ms


# --------------------------------------------------------------------------- timeline


class Cut(BaseModel):
    """Remove [start_ms, end_ms) of shared time from the output."""

    id: str
    start_ms: int
    end_ms: int
    reason: str = ""


class Segment(BaseModel):
    kind: Literal["intro", "title", "source", "hold", "outro"]  # hold: last footage frame, still
    out_start_ms: int
    out_end_ms: int
    shared_start_ms: int = 0  # kind=source: first instant shown; kind=hold: the frame held
    rate: float = 1.0  # kind=source: footage plays this many times faster (a fast-forwarded pause)
    from_frame: int | None = None  # canonical 30 fps frame schedule, set by build_segments
    duration_frames: int | None = None
    available_roles: list[str] = Field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return self.out_end_ms - self.out_start_ms

    @property
    def shared_ms(self) -> int:
        """Footage (shared time) this segment covers."""
        return round(self.duration_ms * self.rate)


class Timeline(BaseModel):
    intro_ms: int = 3000
    title_ms: int = 0  # item-v1: white title card after the official intro
    outro_ms: int = 0  # item-v1: official closing bookend
    cuts: list[Cut] = Field(default_factory=list)
    # Silent footage removed automatically before the first narrated action / after the last line (set by tts).
    trim_head_ms: int = 0
    trim_tail_ms: int = 0
    hold_ms: int = 0  # last footage frame held still so closing narration can finish (set by tts)
    # Quiet shared-time ranges played at SPEEDUP_RATE (set by tts): long waits such as a screen loading.
    speedups: list[list[int]] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)  # derived; rebuilt by stages.timeline
    duration_ms: int = 0

    def extent(self, seg: Segment) -> int:
        """Where narration starting in ``seg`` must end: its end, or the end of a hold right after it."""
        nxt = next((s for s in self.segments if s.out_start_ms == seg.out_end_ms), None)
        return nxt.out_end_ms if nxt is not None and nxt.kind == "hold" else seg.out_end_ms

    def removed(self, shared_duration_ms: int) -> list[list[int]]:
        """Merged shared-time ranges left out of the output: reviewer cuts plus the head/tail trim."""
        spans = [(max(0, c.start_ms), min(shared_duration_ms, c.end_ms)) for c in self.cuts]
        spans += [(0, self.trim_head_ms), (shared_duration_ms - self.trim_tail_ms, shared_duration_ms)]
        merged: list[list[int]] = []
        for s, e in sorted(sp for sp in spans if sp[1] > sp[0]):
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        return merged

    def to_output(self, shared_ms: int) -> int | None:
        """Shared time -> output time; None when the instant was cut."""
        sources = [s for s in self.segments if s.kind == "source"]
        for seg in sources:
            offset = shared_ms - seg.shared_start_ms
            if 0 <= offset < seg.shared_ms or (offset == seg.shared_ms and seg is sources[-1]):
                return seg.out_start_ms + min(seg.duration_ms, round(offset / seg.rate))
        return None

    def to_shared(self, out_ms: int) -> int | None:
        for seg in self.segments:
            if seg.kind == "source" and seg.out_start_ms <= out_ms < seg.out_end_ms:
                return seg.shared_start_ms + round((out_ms - seg.out_start_ms) * seg.rate)
        return None

    def segment_at_output(self, out_ms: int) -> Segment | None:
        for seg in self.segments:
            if seg.out_start_ms <= out_ms < seg.out_end_ms:
                return seg
        return None


# --------------------------------------------------------------------------- evidence


class TranscriptWord(BaseModel):
    role: Literal["physical", "system"]
    start_ms: int  # shared time
    end_ms: int
    text: str
    prob: float = 0.0


class TranscriptSegment(BaseModel):
    role: Literal["physical", "system"]
    start_ms: int
    end_ms: int
    text: str
    words: list[TranscriptWord] = Field(default_factory=list)
    language: str = ""  # detected per segment, e.g. "en" or "es"
    text_en: str = ""  # English rendering of `text` (same as text when already English)


class CandidateWindow(BaseModel):
    start_ms: int  # shared time
    end_ms: int
    signals: list[str] = Field(default_factory=list)  # scene-cut | screen-change | motion | audio-peak
    view_hint: View = "both"
    strength: float = 0.0


class OcrHit(BaseModel):
    text: str
    score: float
    box: list[int] = Field(default_factory=list)  # x0,y0,x1,y1 in system-source pixels


class Event(BaseModel):
    id: str
    start_ms: int  # shared time
    end_ms: int
    view: View
    action: str  # what was observed, plain language
    kind: Literal["action", "result"] = "action"  # result = visible outcome (success message, label printed, ...)
    visible_text: list[str] = Field(default_factory=list)  # exact control / screen text the VLM read
    ocr: list[OcrHit] = Field(default_factory=list)
    ocr_frame_ms: int | None = None
    confidence: float = 0.5
    source: Literal["vlm-coarse", "vlm-fine", "mock", "human"] = "vlm-coarse"
    sop_step_id: str | None = None
    evidence_status: EvidenceStatus | None = None
    transcript_context: str = ""  # supporting context only — never proof
    review_required: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    approved: bool = False  # legacy; kept so older project files load
    rejected: bool = False  # removed by the reviewer (soft delete, restorable)
    human_edited: bool = False
    narration: str | None = None  # reviewer-written narration line for this action

    @property
    def placeholder(self) -> bool:
        """A signal-only detection with no description, step or narration yet (moving it doesn't count)."""
        return self.action in PLACEHOLDER_ACTIONS and self.sop_step_id is None and not self.narration

    @property
    def trusted(self) -> bool:
        """Counts as evidence: a person touched it, or the model was confident. No approval needed."""
        return not self.rejected and not self.placeholder and (self.human_edited or self.confidence >= 0.6)


# --------------------------------------------------------------------------- SOP


class SopStep(BaseModel):
    id: str  # stable: step_01, step_02 ... (or the SOP's own numbering)
    number: str
    section: str = ""
    title: str = ""  # short name: the bold lead-in, or the first sentence
    text: str
    controls: list[str] = Field(default_factory=list)  # exact control names expected (bold, `code`, "quoted")
    expected_view: View = "both"
    completion_evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)


class Discrepancy(BaseModel):
    kind: Literal[
        "out-of-order",
        "not-in-sop",
        "control-mismatch",
        "speech-only",
        "missing-completion-evidence",
        "low-confidence",
        "ocr-disagrees",
    ]
    message: str
    step_id: str | None = None
    event_id: str | None = None


class StepCoverage(BaseModel):
    step_id: str
    status: EvidenceStatus
    event_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    human_override: EvidenceStatus | None = None

    @property
    def effective(self) -> EvidenceStatus:
        return self.human_override or self.status


class Coverage(BaseModel):
    steps: list[StepCoverage] = Field(default_factory=list)
    discrepancies: list[Discrepancy] = Field(default_factory=list)
    method: str = "deterministic"


# --------------------------------------------------------------------------- narration


class NarrationCue(BaseModel):
    id: str
    event_id: str
    text: str
    kind: Literal["action", "result"] = "action"
    sop_step_id: str | None = None
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    approved: bool = False
    human_edited: bool = False
    # filled by tts / scheduling
    spoken_text: str = ""  # auto-shortened wording actually voiced ("" = same as text)
    audio: str = ""  # work/tts/<hash>.wav
    audio_hash: str = ""
    duration_ms: int = 0
    out_start_ms: int | None = None  # output time
    out_end_ms: int | None = None
    anchor_out_ms: int | None = None  # where the event starts in output time
    schedule_notes: list[str] = Field(default_factory=list)
    dropped: bool = False  # event was cut or cue could not be placed
    phase: Literal["opener", "title", "operational"] = "operational"
    from_frame: int | None = None  # set by frameclock.resolve_cues
    end_frame: int | None = None
    duration_frames: int | None = None


class Caption(BaseModel):
    index: int
    cue_id: str
    start_ms: int  # output time
    end_ms: int
    text: str
    from_frame: int | None = None
    end_frame: int | None = None


class LayoutKey(BaseModel):
    out_start_ms: int
    out_end_ms: int
    focus: Literal["system", "physical", "split"]
    heading: str = ""
    step_id: str | None = None
    step_index: int | None = None  # 1-based position in SOP for the progress indicator


# --------------------------------------------------------------------------- QA / project


class QACheck(BaseModel):
    name: str
    ok: bool
    detail: str = ""
    severity: Literal["error", "warning", "info"] = "error"


class QAReport(BaseModel):
    target: str
    checks: list[QACheck] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.severity == "error")


class StageStatus(BaseModel):
    state: StageState = "pending"
    input_hash: str = ""
    started_at: str = ""
    finished_at: str = ""
    message: str = ""
    duration_s: float = 0.0


class HumanAction(BaseModel):
    at: str = Field(default_factory=now_iso)
    action: str
    detail: dict = Field(default_factory=dict)


class Review(BaseModel):
    approved: bool = False  # "Render final video" clicked; any later edit clears it
    auto_approve: bool = False  # skip the review stop (CI / batch use)


PLACEHOLDER_ACTIONS = {"Screen change on the WISE system", "Physical handling motion", "Activity in both views", "New action"}


class Project(BaseModel):
    id: str
    title: str
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    template: str = "legacy"  # "item-v1": official Item bookends, title card and caption bar
    physical: SourceMedia | None = None
    system: SourceMedia | None = None
    sop: SopDoc | None = None

    stages: dict[str, StageStatus] = Field(default_factory=dict)
    sync_override_ms: int | None = None  # reviewer-set lag; wins over every estimator
    sync: SyncResult | None = None
    timeline: Timeline = Field(default_factory=Timeline)
    transcript: list[TranscriptSegment] = Field(default_factory=list)
    transcript_status: str = ""
    windows: list[CandidateWindow] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    sop_steps: list[SopStep] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)
    cues: list[NarrationCue] = Field(default_factory=list)
    captions: list[Caption] = Field(default_factory=list)
    layout: list[LayoutKey] = Field(default_factory=list)
    qa: list[QAReport] = Field(default_factory=list)
    review: Review = Field(default_factory=Review)
    history: list[HumanAction] = Field(default_factory=list)
    provenance: dict = Field(default_factory=dict)  # model / tool versions + settings per stage

    def event(self, event_id: str) -> Event | None:
        return next((e for e in self.events if e.id == event_id), None)

    def step(self, step_id: str | None) -> SopStep | None:
        return next((s for s in self.sop_steps if s.id == step_id), None)

    def live_events(self) -> list[Event]:
        return sorted((e for e in self.events if not e.rejected), key=lambda e: (e.start_ms, e.end_ms))
