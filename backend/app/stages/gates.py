"""The single human review stop, just before the final render.

Nothing per item blocks: everything left on the timeline counts. The reviewer edits on the
Edit & review screen (live preview in the browser) and clicks "Render final video".
"""

from __future__ import annotations

from ..pipeline import AwaitReview, Ctx, Stage


def _inputs(p):
    return {"approved": p.review.approved or p.review.auto_approve}


def run(ctx: Ctx) -> str:
    p = ctx.project
    if p.review.approved or p.review.auto_approve:
        return "approved"
    hints = sum(e.placeholder and not e.rejected for e in p.events)
    lines = sum(not c.dropped for c in p.cues)
    note = f"{lines} narration line(s)"
    if hints:
        note += f"; {hints} detected moment(s) not described yet (they are skipped unless you describe them)"
    raise AwaitReview(f"ready to review — {note}. Check it in Edit & review, then click Render final video")


REVIEW = Stage("review", "Review", "3", _inputs, run, gate=True)
