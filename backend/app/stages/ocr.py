"""10. Verify WISE text separately: PaddleOCR on full-resolution system frames of system events.

OCR complements the VLM: OCR reads text, the VLM interprets the action. Disagreement on a control
name or value flags the event for review. Without PaddleOCR, a configured VLM does a dedicated
read-text pass on the same frame; with neither, VLM-read text stays unverified.
"""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

from ..config import settings
from ..media import extract_frame
from ..models import OcrHit
from ..pipeline import Ctx, Skip, Stage
from ..providers.vlm import get_vlm

log = logging.getLogger(__name__)
_paddle = None


def _inputs(p):
    return {"events": [[e.id, e.start_ms, e.end_ms, e.view, e.kind, e.visible_text] for e in p.events],
            "sync": [p.sync.lag_ms, p.sync.shared_start_physical_ms], "vlm": settings.vlm_provider}


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def text_present(needle: str, haystack: list[str]) -> bool:
    n = norm(needle)
    if not n:
        return True
    joined = " " + " ".join(norm(h) for h in haystack) + " "
    return f" {n} " in joined or any(n == norm(h) for h in haystack)


def _paddle_ocr():
    global _paddle
    if _paddle is None:
        from paddleocr import PaddleOCR  # type: ignore

        _paddle = PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)
    return _paddle


def _run_paddle(img: Path) -> list[OcrHit]:
    hits = []
    for res in _paddle_ocr().predict(str(img)):
        data = res.json.get("res", res.json) if hasattr(res, "json") else res
        for text, score, box in zip(data.get("rec_texts", []), data.get("rec_scores", []), data.get("rec_boxes", [])):
            hits.append(OcrHit(text=text, score=round(float(score), 3), box=[int(v) for v in list(box)[:4]]))
    return hits


def run(ctx: Ctx) -> str:
    p = ctx.project
    try:
        import paddleocr  # noqa: F401
        engine = "paddleocr"
    except ImportError:
        vlm = get_vlm()
        # A paid model re-reading its own frames costs one call per event and verifies little; skip it.
        engine = "vlm" if vlm.available() and vlm.name != "anthropic" else ""
    targets = [e for e in p.events if e.view in ("system", "both") and not e.rejected]
    if not engine:
        for e in targets:
            if e.visible_text and "visible text not OCR-verified" not in e.review_reasons:
                e.review_reasons.append("visible text not OCR-verified")
        raise Skip("no OCR engine (uv sync --extra ocr) and no separate OCR model — text left unverified")

    vlm = get_vlm()
    checked = disagreements = 0
    with tempfile.TemporaryDirectory() as tmp:
        for e in targets:
            # results are judged near their end (state settled); actions mid-way
            t_shared = e.end_ms - 150 if e.kind == "result" else (e.start_ms + e.end_ms) // 2
            img = extract_frame(ctx.system_path(), p.sync.system_ms(t_shared), Path(tmp) / f"{e.id}.jpg")
            try:
                if engine == "paddleocr":
                    e.ocr = _run_paddle(img)
                else:
                    e.ocr = [OcrHit(text=t, score=0.5) for t in vlm.read_text(img.read_bytes())]
            except Exception as exc:  # noqa: BLE001
                log.warning("ocr %s failed: %s", e.id, exc)
                continue
            e.ocr_frame_ms = t_shared
            checked += 1
            texts = [h.text for h in e.ocr if h.score >= 0.5]
            missing = [t for t in e.visible_text if not text_present(t, texts)]
            e.review_reasons = [r for r in e.review_reasons if not r.startswith(("OCR could not confirm", "visible text not"))]
            if missing:
                disagreements += 1
                e.review_required = True
                e.review_reasons.append(f"OCR could not confirm: {', '.join(missing)}")
    ctx.provenance("ocr", engine=engine)
    return f"{checked} system frames read with {engine}; {disagreements} text disagreements flagged"


STAGE = Stage("ocr", "Verify WISE text (OCR)", "1", _inputs, run)
