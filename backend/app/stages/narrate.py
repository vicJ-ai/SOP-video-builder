"""13-14. One short narration line per relevant, verified event.

Lines describe what the footage shows (not what the SOP says should happen), use exact visible
control names, and are flagged for review when the underlying evidence is uncertain. Human edits
are preserved across reruns.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from ..models import PLACEHOLDER_ACTIONS, NarrationCue
from ..narration import exact_controls, imperative, sentence, unverified_names
from ..pipeline import Ctx, Stage
from ..providers.llm import get_llm

log = logging.getLogger(__name__)


def _inputs(p):
    return {
        "events": [e.model_dump(exclude={"transcript_context"}) for e in p.live_events()],
        "steps": [[s.id, s.title, s.text, s.controls] for s in p.sop_steps],
    }


class Polished(BaseModel):
    text: str


def _polish(llm, line: str, names: list[str]) -> str:
    try:
        out = llm.structured([
            {"role": "system", "content": "Rewrite warehouse training narration as one short, plain imperative or "
                                          "present-tense sentence (max 14 words). Keep these exact control names "
                                          f"unchanged: {names}. Add no new facts, names or steps."},
            {"role": "user", "content": line},
        ], Polished).text.strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("polish failed: %s", exc)
        return line
    # semantic guard: every control name must survive, nothing new may be invented
    if any(n.lower() not in out.lower() for n in names if n.lower() in line.lower()) or unverified_names(out, names):
        return line
    return sentence(out)


def auto_line(e, step, llm=None) -> str:
    """Default narration for an event when the reviewer has not written one."""
    if e.action in PLACEHOLDER_ACTIONS and step is not None:
        # a person placed this SOP step on an undescribed moment: say the step
        return sentence(step.title or step.text)
    verified = [h.text for h in e.ocr if h.score >= 0.5]
    names = list(dict.fromkeys(verified + e.visible_text))
    line = sentence(e.action) if e.kind == "result" else imperative(e.action)
    line = exact_controls(line, names)
    return _polish(llm, line, names) if llm is not None else line


def run(ctx: Ctx) -> str:
    p = ctx.project
    previous = {c.event_id: c for c in p.cues}
    llm = get_llm()
    cues: list[NarrationCue] = []
    for e in p.live_events():
        # narrate SOP steps and anything a person described; unscripted model detections stay
        # in the coverage report as "not in SOP"; undescribed detections are only hints
        if e.placeholder or not (e.sop_step_id is not None or e.human_edited):
            continue
        old = previous.get(e.id)
        if e.narration is None and old and old.human_edited:
            e.narration = old.text  # carry over edits made before narration moved onto the event
        step = p.step(e.sop_step_id)
        line = sentence(e.narration) if e.narration else auto_line(e, step, llm)
        reasons = []  # informational only: highlighted in the editor, never blocking
        if not e.human_edited and not e.narration:
            if e.confidence < 0.6:
                reasons.append(f"model confidence {e.confidence:.0%}")
            verified = [h.text for h in e.ocr if h.score >= 0.5]
            if bad := unverified_names(line, verified or e.visible_text):
                reasons.append(f"control name not seen on screen: {', '.join(bad)}")
        cues.append(NarrationCue(
            id=f"cue-{e.id.removeprefix('ev-')}", event_id=e.id, text=line, kind=e.kind, sop_step_id=e.sop_step_id,
            needs_review=bool(reasons), review_reasons=reasons, human_edited=bool(e.narration), approved=True,
        ))
    p.cues = cues
    return f"{len(cues)} narration lines"


STAGE = Stage("narrate", "Write narration", "2", _inputs, run)
