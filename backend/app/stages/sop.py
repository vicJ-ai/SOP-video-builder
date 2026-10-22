"""11-12. Parse the SOP, then compare it with the observed (verified) action timeline.

Expected (SOP) and observed (footage) stay separate. Each step becomes demonstrated / partial /
missing / unclear, and differences are flagged — the app never invents an action so that the video
matches the instructions. Speech can explain, but never proves, that a step happened.
"""

from __future__ import annotations

import logging
import math
import re

from pydantic import BaseModel, Field

from ..models import Coverage, Discrepancy, Event, StepCoverage
from ..pipeline import Ctx, Skip, Stage
from ..providers.errors import ClaudeAccountError
from ..providers.llm import get_llm
from ..sopparse import parse_intro, parse_sop
from .ocr import text_present

log = logging.getLogger(__name__)
MATCH_THRESHOLD = 0.25
STOP = set("the a an and or to of in on at for with from by is are be this that then it its your you into onto "
           "as all any each will should must when once after before until if not do does".split())


def _parse_inputs(p):
    if p.mode == "demo":
        return {"demo": True}
    return {"sop": p.sop.sha256}


def run_parse(ctx: Ctx) -> str:
    p = ctx.project
    if p.mode == "demo":
        raise Skip("demo video: no SOP")
    text = (ctx.originals / p.sop.filename).read_text(encoding="utf-8-sig")
    title, steps = parse_sop(text)
    p.sop.title = title or p.sop.title
    p.sop.purpose = parse_intro(text)
    p.sop_steps = steps
    if not steps:
        raise ValueError("no steps found in the SOP — use a numbered list or 'Step N' headings")
    return f"{len(steps)} steps, {sum(len(s.controls) for s in steps)} named controls"


PARSE = Stage("sop_parse", "Parse SOP", "3", _parse_inputs, run_parse)


# --------------------------------------------------------------------------- comparison


def _tokens(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if len(w) < 3 or w in STOP:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                w = w[: -len(suf)]
                break
        out.add(w)
    return out


def _event_texts(e: Event) -> list[str]:
    return e.visible_text + [h.text for h in e.ocr if h.score >= 0.5]


def idf_weights(steps) -> dict[str, float]:
    """Words shared by many steps (e.g. "pallet") carry little evidence about which step this is."""
    df: dict[str, int] = {}
    for s in steps:
        for t in _tokens(s.text):
            df[t] = df.get(t, 0) + 1
    return {t: math.log(1 + len(steps) / n) for t, n in df.items()}


def score(e: Event, step, idf: dict[str, float] | None = None) -> float:
    st = _tokens(step.text)
    if not st:
        return 0.0
    et = _tokens(e.action + " " + " ".join(_event_texts(e)))
    w = idf or {}
    s = sum(w.get(t, 1.0) for t in st & et) / sum(w.get(t, 1.0) for t in st)
    texts = _event_texts(e) + [e.action]
    hits = sum(1 for c in step.controls if text_present(c, texts))
    s += min(1.0, 0.5 * hits)
    if step.expected_view != "both" and e.view != "both" and e.view != step.expected_view:
        s *= 0.5
    return round(s, 4)


def align(events: list[Event], steps) -> dict[str, str | None]:
    """Order-preserving assignment (DP) of events to SOP steps maximising total match score."""
    n, m = len(events), len(steps)
    if not n or not m:
        return {e.id: None for e in events}
    idf = idf_weights(steps)
    sc = [[score(e, s, idf) for s in steps] for e in events]
    for i, e in enumerate(events):  # human assignments are fixed
        if e.human_edited and e.sop_step_id:
            for j, s in enumerate(steps):
                sc[i][j] = 100.0 if s.id == e.sop_step_id else -100.0
    NEG = float("-inf")
    best = [[0.0] * m for _ in range(n + 1)]
    choice = [[None] * m for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(m):
            skip = best[i - 1][j]
            take = best[i - 1][j] + sc[i - 1][j] if sc[i - 1][j] >= MATCH_THRESHOLD else NEG
            carry = best[i][j - 1] if j else NEG
            options = [(skip, ("skip", j)), (take, ("take", j)), (carry, ("carry", j - 1))]
            val, how = max(options, key=lambda o: o[0])
            best[i][j], choice[i][j] = val, how
    out: dict[str, str | None] = {}
    i, j = n, m - 1
    while i > 0:
        kind, jj = choice[i][j]
        if kind == "carry":
            j = jj
            continue
        out[events[i - 1].id] = steps[j].id if kind == "take" else None
        i -= 1
    return out


class LlmAssign(BaseModel):
    event_id: str
    step_id: str | None = None


class LlmAlignment(BaseModel):
    assignments: list[LlmAssign] = Field(default_factory=list)


def _llm_refine(events: list[Event], steps, proposed: dict[str, str | None]) -> dict[str, str | None] | None:
    llm = get_llm()
    if llm is None:
        return None
    payload = {
        "steps": [{"id": s.id, "text": s.text, "controls": s.controls} for s in steps],
        "events": [{"id": e.id, "t": [e.start_ms, e.end_ms], "view": e.view, "action": e.action,
                    "visible_text": _event_texts(e), "proposed_step": proposed.get(e.id)} for e in events],
    }
    try:
        res = llm.structured([
            {"role": "system", "content": "Match observed warehouse actions to SOP steps. Use only the given ids. "
                                          "An event may match no step (null). Never assume an unobserved action."},
            {"role": "user", "content": str(payload)},
        ], LlmAlignment)
    except ClaudeAccountError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM refinement failed: %s", exc)
        return None
    ids = {e.id for e in events}
    step_ids = {s.id for s in steps}
    out = dict(proposed)
    for a in res.assignments:
        if a.event_id in ids and (a.step_id is None or a.step_id in step_ids):
            out[a.event_id] = a.step_id
    return out


def _transcript_near(p, e: Event) -> str:
    seg = [s.text_en or s.text for s in p.transcript if s.end_ms >= e.start_ms - 1000 and s.start_ms <= e.end_ms + 1000]
    return " ".join(seg)[:400]


def _compare_inputs(p):
    return {
        "steps": [s.model_dump() for s in p.sop_steps],
        "events": [e.model_dump(exclude={"sop_step_id", "evidence_status", "transcript_context", "narration"})
                   for e in p.events],
        "assigned": [[e.id, e.sop_step_id] for e in p.events if e.human_edited],
        "transcript": [[s.start_ms, s.end_ms, s.text_en or s.text] for s in p.transcript],
        "overrides": {c.step_id: c.human_override for c in p.coverage.steps if c.human_override},
    }


def run_compare(ctx: Ctx) -> str:
    p = ctx.project
    if p.mode == "demo":
        raise Skip("demo video: no SOP to compare")
    steps = p.sop_steps
    # undescribed signal-only detections are hints, never evidence
    events = [e for e in p.live_events() if not e.placeholder]
    for e in events:
        e.transcript_context = _transcript_near(p, e)
    assigned = align(events, steps)
    method = "deterministic"
    refined = _llm_refine(events, steps, assigned)
    if refined is not None:
        assigned, method = refined, "deterministic+llm"

    overrides = {c.step_id: c.human_override for c in p.coverage.steps if c.human_override}
    discrepancies: list[Discrepancy] = []
    coverage: list[StepCoverage] = []
    order = {s.id: i for i, s in enumerate(steps)}
    idf = idf_weights(steps)

    for e in events:
        e.sop_step_id = assigned.get(e.id)
        if e.sop_step_id is None and e.trusted:
            discrepancies.append(Discrepancy(kind="not-in-sop", event_id=e.id,
                                             message=f"Observed action not described in the SOP: “{e.action}”"))
        # out-of-order: event clearly matches a different step than its order-preserving assignment
        best_j = max(range(len(steps)), key=lambda j: score(e, steps[j], idf)) if steps else None
        if best_j is not None:
            best_s = score(e, steps[best_j], idf)
            cur = next((s for s in steps if s.id == e.sop_step_id), None)
            cur_s = score(e, cur, idf) if cur else 0.0
            if steps[best_j].id != e.sop_step_id and best_s >= MATCH_THRESHOLD + 0.25 and best_s > cur_s + 0.25:
                discrepancies.append(Discrepancy(
                    kind="out-of-order", event_id=e.id, step_id=steps[best_j].id,
                    message=f"“{e.action}” looks like step {steps[best_j].number} but happens out of SOP order"))

    for s in steps:
        evs = [e for e in events if e.sop_step_id == s.id]
        notes: list[str] = []
        if not evs:
            spoken = [seg for seg in p.transcript  # English rendering: the SOP is English, speech may be Spanish
                      if len(_tokens(s.text) & _tokens(seg.text_en or seg.text)) >= max(2, len(_tokens(s.text)) // 2)]
            if spoken:
                status = "unclear"
                notes.append("mentioned in speech but not visibly demonstrated")
                discrepancies.append(Discrepancy(kind="speech-only", step_id=s.id,
                                                 message=f"Step {s.number} is spoken about at {spoken[0].start_ms/1000:.1f}s "
                                                         "but not visible in the footage"))
            else:
                status = "missing"
        else:
            status = "demonstrated"
            reviewer = any(e.human_edited for e in evs)  # a person placed this step on the footage
            if not any(e.trusted for e in evs):
                status = "unclear"
                notes.append("only low-confidence observations")
            texts = [t for e in evs for t in _event_texts(e) + [e.action]]
            missing_controls = [c for c in s.controls if not text_present(c, texts)]
            if missing_controls and reviewer:
                notes.append(f"control(s) not named in the description: {', '.join(missing_controls)}")
            elif missing_controls and status == "demonstrated":
                status = "partial"
                notes.append(f"control(s) not seen: {', '.join(missing_controls)}")
                seen = sorted({t for e in evs for t in _event_texts(e)})
                if seen:
                    discrepancies.append(Discrepancy(
                        kind="control-mismatch", step_id=s.id,
                        message=f"Step {s.number} expects {', '.join(missing_controls)}; screen shows {', '.join(seen[:4])}"))
            if s.completion_evidence and not reviewer and not any(e.kind == "result" for e in evs):
                if status == "demonstrated":
                    status = "partial"
                notes.append("no visible completion evidence")
                discrepancies.append(Discrepancy(kind="missing-completion-evidence", step_id=s.id,
                                                 message=f"Step {s.number}: “{s.completion_evidence[0]}” is not visibly confirmed"))
        for e in evs:
            e.evidence_status = status
        coverage.append(StepCoverage(step_id=s.id, status=status, event_ids=[e.id for e in evs], notes=notes,
                                     human_override=overrides.get(s.id)))

    discrepancies.sort(key=lambda d: order.get(d.step_id or "", 10_000))
    p.coverage = Coverage(steps=coverage, discrepancies=discrepancies, method=method)
    counts = {k: sum(c.effective == k for c in coverage) for k in ("demonstrated", "partial", "missing", "unclear")}
    return ", ".join(f"{v} {k}" for k, v in counts.items()) + f"; {len(discrepancies)} flags"


COMPARE = Stage("compare", "Compare with SOP", "1", _compare_inputs, run_compare)
