from __future__ import annotations

from f117.domain import AIVerdict

_AUTOMATIC_DELIVERABLE_AI_VERDICTS = frozenset(
    {
        AIVerdict.STRONG,
        AIVerdict.INTERESTING,
    }
)


def is_automatic_ai_verdict_deliverable(verdict: AIVerdict) -> bool:
    """Return whether an AI verdict may enter an automatic Radar delivery."""

    return verdict in _AUTOMATIC_DELIVERABLE_AI_VERDICTS


def is_ai_verdict_visible(verdict: AIVerdict, *, manual: bool) -> bool:
    """Manual owner submissions are always shown; automatic delivery is strict."""

    return manual or is_automatic_ai_verdict_deliverable(verdict)
