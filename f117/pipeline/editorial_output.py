"""Validation and bounded repair for the final critical editorial paragraph."""

from __future__ import annotations

import re

_SENTENCE_RE = re.compile(r"[^.!?]+[.!?](?=\s|$)", re.DOTALL)
_SPACE_RE = re.compile(r"\s+")
_BAD_ENDINGS = ("...", "…", ",", ":", ";")


def normalize_ai_opinion(value: str) -> str:
    """Keep only complete sentences; never cut the paragraph mid-sentence."""

    cleaned = _SPACE_RE.sub(" ", value).strip()
    if not cleaned or cleaned.endswith(_BAD_ENDINGS):
        raise ValueError("AI opinion must end with a complete sentence")

    sentences = [
        _SPACE_RE.sub(" ", match.group(0)).strip() for match in _SENTENCE_RE.finditer(cleaned)
    ]
    if not sentences or "".join(sentences).replace(" ", "") != cleaned.replace(" ", ""):
        raise ValueError("AI opinion contains an unfinished sentence")

    # Prefer the latest complete conclusions when the model was verbose.
    candidates = sentences[-4:]
    while len(" ".join(candidates)) > 600 and len(candidates) > 2:
        candidates.pop(0)
    result = " ".join(candidates)
    if len(result) > 600:
        raise ValueError("AI opinion is too long to repair without truncation")
    if not 2 <= len(candidates) <= 4:
        raise ValueError("AI opinion must contain 2-4 complete sentences")
    if len(result) < 250:
        raise ValueError("AI opinion is too short")
    return result
