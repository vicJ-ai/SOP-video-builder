"""Deterministic narration writing helpers (no model required)."""

from __future__ import annotations

import re

SUBJECT_RE = re.compile(
    r"^(?:the\s+)?(?:worker|operator|user|associate|employee|picker|person|he|she|they|someone)\s+(?:then\s+)?", re.I)
MAX_WORDS = 16
FILLER = re.compile(r"\b(?:carefully|then|now|simply|just|basically|quickly|slowly|gently|on the screen|in the system)\b\s*", re.I)


def _base_verb(word: str) -> str:
    w = word.lower()
    if w in ("is", "was"):
        return word
    if w.endswith("ies") and len(w) > 4:
        return word[:-3] + "y"
    if re.search(r"(sses|ches|shes|xes|zzes)$", w):
        return word[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def imperative(action: str) -> str:
    """'The worker taps Confirm' -> 'Tap Confirm.'"""
    text = action.strip().rstrip(".")
    stripped = SUBJECT_RE.sub("", text)
    if stripped != text and stripped:
        first, _, rest = stripped.partition(" ")
        text = f"{_base_verb(first)} {rest}".strip()
    return sentence(text)


def sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    if not text:
        return ""
    return text[0].upper() + text[1:] + "."


def exact_controls(text: str, names: list[str]) -> str:
    """Use the exact on-screen spelling/casing of control names that the line mentions."""
    for name in sorted(set(names), key=len, reverse=True):
        if len(name) < 2:
            continue
        text = re.sub(rf"(?<![\w]){re.escape(name)}(?![\w])", name, text, flags=re.I)
    return text


def shorten(text: str) -> str:
    """One deterministic shortening pass: drop fillers, parentheticals, then trailing clauses."""
    original = text
    text = re.sub(r"\s*\([^)]*\)", "", text)
    text = FILLER.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if text == original or len(text.split()) > MAX_WORDS:
        parts = re.split(r",\s*|\s+(?:and then|and|so that|which|to make sure)\s+", text.rstrip("."), maxsplit=1)
        if len(parts) > 1 and len(parts[0].split()) >= 2:
            text = parts[0]
    words = text.rstrip(".").split()
    if len(words) > MAX_WORDS:
        text = " ".join(words[:MAX_WORDS])
    return sentence(text)


def unverified_names(text: str, verified: list[str]) -> list[str]:
    """Quoted / Title-Case control-like tokens in the line that are not backed by visible text."""
    candidates = re.findall(r"[\"“]([^\"”]+)[\"”]", text)
    candidates += re.findall(r"\b(?:tap|press|click|select|choose)\s+((?:[A-Z][\w/-]*\s?){1,4})", text)
    low = [v.lower() for v in verified]
    return [c.strip() for c in candidates if c.strip() and not any(c.strip().lower() in v or v in c.strip().lower()
                                                                     for v in low)]
