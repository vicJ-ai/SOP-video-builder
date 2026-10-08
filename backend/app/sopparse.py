"""Parse an SOP Markdown file into stable, structured steps."""

from __future__ import annotations

import re

from .models import SopStep

STEP_RE = re.compile(r"^(?P<indent>\s*)(?P<num>\d+(?:\.\d+)*)[.)]\s+(?P<text>.+)$")
STEP_HEADING_RE = re.compile(r"^#{2,6}\s*Step\s+(?P<num>\d+(?:\.\d+)*[a-z]?)\s*[:.\-–—]?\s*(?P<text>.*)$", re.I)
HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*#*$")
BULLET_RE = re.compile(r"^(?P<indent>\s*)[-*+]\s+(?P<text>.+)$")
WARN_RE = re.compile(r"^(?:\*\*|__)?(warning|caution|important|note|⚠️?)\b[:\s]*", re.I)
PREREQ_RE = re.compile(r"prerequisite|before you (?:begin|start)|requirements|you will need|materials", re.I)
EVIDENCE_RE = re.compile(
    r"[^.;]*\b(until|confirm\w*|message|display\w*|appear\w*|shows?|success\w*|complet\w*|status|turns?|prints?)\b[^.;]*",
    re.I)

CONTROL_PATTERNS = [
    re.compile(r"\*\*(.+?)\*\*"),
    re.compile(r"__(.+?)__"),
    re.compile(r"`([^`]+)`"),
    re.compile(r"[\"“](.+?)[\"”]"),
    re.compile(r"(?<!!)\[([^\]]+)\](?!\()"),
]

SYSTEM_WORDS = {"tap", "click", "select", "press", "enter", "type", "screen", "wise", "menu", "button", "field", "app",
                "login", "log", "tab", "dropdown", "submit", "confirm", "system", "input", "search", "open", "save",
                "display", "dialog", "popup", "rf", "terminal", "keyboard"}
PHYSICAL_WORDS = {"pick", "place", "lift", "move", "pallet", "carton", "box", "shelf", "forklift", "wrap", "stage",
                  "dock", "carry", "count", "put", "load", "unload", "rack", "bin", "tote", "cart", "walk", "drive",
                  "stack", "apply", "attach", "inspect", "seal", "tape", "jack", "trailer", "location", "label"}


def _clean(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # links -> text
    return re.sub(r"\s+", " ", text).strip()


def controls_in(text: str) -> list[str]:
    found: list[str] = []
    for pat in CONTROL_PATTERNS:
        for m in pat.finditer(text):
            c = m.group(1).strip().strip(".,:;")
            if 0 < len(c) <= 40 and c not in found and not WARN_RE.match(c + " "):
                found.append(c)
    return found


def plain(text: str) -> str:
    return _clean(re.sub(r"(\*\*|__|`)", "", text))


def expected_view(text: str) -> str:
    words = set(re.findall(r"[a-z]+", text.lower()))
    s, p = len(words & SYSTEM_WORDS), len(words & PHYSICAL_WORDS)
    if "scan" in words:
        s, p = s + 1, p + 1
    if s and p:
        return "both"
    return "system" if s else "physical" if p else "both"


def parse_sop(markdown: str) -> tuple[str, list[SopStep]]:
    title = ""
    section = ""
    in_prereq = False
    prereqs: list[str] = []
    raw: list[dict] = []
    current: dict | None = None

    for line in markdown.splitlines():
        if not line.strip():
            continue
        if m := STEP_HEADING_RE.match(line):
            current = {"num": m["num"], "lines": [m["text"]], "section": section, "warn": [], "depth": 0}
            raw.append(current)
            in_prereq = False
            continue
        if m := HEADING_RE.match(line):
            text = plain(m["text"])
            if len(m["hashes"]) == 1 and not title:
                title = text
            else:
                section = text
            in_prereq = bool(PREREQ_RE.search(text))
            current = None
            continue
        if m := STEP_RE.match(line):
            if in_prereq:
                prereqs.append(plain(m["text"]))
                continue
            depth = len(m["indent"].expandtabs(4)) // 2
            num = m["num"]
            if depth and raw and current is not None:
                parent = raw[-1] if raw[-1]["depth"] == 0 else next(r for r in reversed(raw) if r["depth"] == 0)
                num = f"{parent['num']}.{num}"
            current = {"num": num, "lines": [m["text"]], "section": section, "warn": [], "depth": 1 if depth else 0}
            raw.append(current)
            continue
        body = line.strip()
        bullet = BULLET_RE.match(line)
        if bullet:
            body = bullet["text"]
        if in_prereq:
            prereqs.append(plain(body))
            continue
        if WARN_RE.match(body.lstrip("> ")):
            target = current["warn"] if current else prereqs
            target.append(plain(WARN_RE.sub("", body.lstrip("> "))))
            continue
        if current is not None:
            current["lines"].append(body)

    if not raw:  # SOP written as plain bullets
        for line in markdown.splitlines():
            if b := BULLET_RE.match(line):
                raw.append({"num": str(len(raw) + 1), "lines": [b["text"]], "section": "", "warn": [], "depth": 0})

    nums = [r["num"] for r in raw]
    unique = len(set(nums)) == len(nums)
    steps: list[SopStep] = []
    for i, r in enumerate(raw, start=1):
        number = r["num"] if unique else str(i)
        # "1. **Confirm the request**" — a bold lead-in is the step's title, not a control name
        lines = list(r["lines"])
        lead = re.match(r"^\s*(\*\*|__)(.+?)\1\s*[:.\-–—]?\s*", lines[0])
        body_for_controls = " ".join([lines[0][lead.end():] if lead else lines[0]] + lines[1:])
        full = " ".join(lines)
        if lead:
            step_title = plain(lead.group(2)).rstrip(".:")
        else:
            step_title = re.split(r"(?<=[.!?])\s", plain(full))[0].rstrip(".")
        text = plain(full)
        ident = "step_" + "_".join(part.zfill(2) for part in re.findall(r"\d+|[a-z]", number))
        steps.append(SopStep(
            id=ident, number=number, section=r["section"], title=step_title, text=text,
            controls=controls_in(body_for_controls),
            expected_view=expected_view(text),
            completion_evidence=[m.group(0).strip() for m in EVIDENCE_RE.finditer(text)][:3],
            warnings=r["warn"], prerequisites=prereqs if i == 1 else [],
        ))
    return title, steps
