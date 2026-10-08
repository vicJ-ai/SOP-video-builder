"""Deterministic caption pages from the measured narration schedule, and SRT / VTT writers."""

from __future__ import annotations

import re

from .models import Caption, NarrationCue

MAX_LINE = 42
MAX_LINES = 2


def _pages(text: str) -> list[str]:
    words = text.split()
    pages, lines, line = [], [], ""
    for w in words:
        if len(line) + len(w) + (1 if line else 0) <= MAX_LINE:
            line = f"{line} {w}".strip()
            continue
        lines.append(line)
        line = w
        if len(lines) == MAX_LINES:
            pages.append("\n".join(lines))
            lines = []
    if line:
        lines.append(line)
    if lines:
        pages.append("\n".join(lines))
    return pages


def build_captions(cues: list[NarrationCue]) -> list[Caption]:
    """Caption timing == narration timing: pages share each cue's interval by character count."""
    out: list[Caption] = []
    for cue in sorted((c for c in cues if not c.dropped and c.out_start_ms is not None), key=lambda c: c.out_start_ms):
        pages = _pages(cue.spoken_text or cue.text)
        total_chars = sum(len(pg) for pg in pages) or 1
        t = cue.out_start_ms
        for i, pg in enumerate(pages):
            end = cue.out_end_ms if i == len(pages) - 1 else t + round((cue.out_end_ms - cue.out_start_ms) * len(pg) / total_chars)
            out.append(Caption(index=len(out) + 1, cue_id=cue.id, start_ms=t, end_ms=end, text=pg))
            t = end
    return out


def _ts(ms: int, sep: str) -> str:
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms_ = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms_:03d}"


def to_srt(captions: list[Caption]) -> str:
    return "\n".join(f"{c.index}\n{_ts(c.start_ms, ',')} --> {_ts(c.end_ms, ',')}\n{c.text}\n" for c in captions)


def to_vtt(captions: list[Caption]) -> str:
    body = "\n".join(f"{c.cue_id}-{c.index}\n{_ts(c.start_ms, '.')} --> {_ts(c.end_ms, '.')}\n{c.text}\n" for c in captions)
    return "WEBVTT\n\n" + body


def parse_srt(text: str) -> list[tuple[int, int, str]]:
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        m = re.match(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", lines[1])
        if not m:
            continue
        v = [int(x) for x in m.groups()]
        out.append(((v[0] * 3600 + v[1] * 60 + v[2]) * 1000 + v[3], (v[4] * 3600 + v[5] * 60 + v[6]) * 1000 + v[7],
                    "\n".join(lines[2:])))
    return out
