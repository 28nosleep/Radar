"""Conservative duplicate detection for normalized materials."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import timedelta
from difflib import SequenceMatcher

from f117.domain import NormalizedItem, StoredMaterial
from f117.pipeline.normalizer import normalize_title

DEFAULT_TITLE_SIMILARITY_THRESHOLD = 0.92
DEFAULT_FUZZY_TIME_WINDOW = timedelta(days=3)
DEFAULT_EXACT_TIME_WINDOW = timedelta(days=7)
_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)


def _tokens(title: str) -> list[str]:
    return _TOKEN_RE.findall(normalize_title(title))


def title_similarity(a: str, b: str) -> float:
    """Return an explainable 0..1 blend of token and sequence similarity."""

    normalized_a = normalize_title(a)
    normalized_b = normalize_title(b)
    if not normalized_a or not normalized_b:
        return 0.0
    if normalized_a == normalized_b:
        return 1.0

    tokens_a = set(_TOKEN_RE.findall(normalized_a))
    tokens_b = set(_TOKEN_RE.findall(normalized_b))
    if not tokens_a or not tokens_b:
        return 0.0

    intersection_size = len(tokens_a & tokens_b)
    jaccard = intersection_size / len(tokens_a | tokens_b)
    containment = intersection_size / min(len(tokens_a), len(tokens_b))
    sequence = SequenceMatcher(None, normalized_a, normalized_b, autojunk=False).ratio()
    return round(0.45 * jaccard + 0.35 * sequence + 0.20 * containment, 6)


def _within_window(a: NormalizedItem, b: NormalizedItem, window: timedelta) -> bool:
    return abs(a.published_at - b.published_at) <= window


def duplicate_reason(
    a: NormalizedItem,
    b: NormalizedItem,
    *,
    threshold: float = DEFAULT_TITLE_SIMILARITY_THRESHOLD,
) -> str | None:
    """Return the decisive duplicate signal, or ``None`` when uncertain."""

    if a.source_key == b.source_key and a.external_id == b.external_id:
        return "same source and external id"
    if a.canonical_url == b.canonical_url:
        return "same canonical URL"

    a_tokens = _tokens(a.normalized_title)
    b_tokens = _tokens(b.normalized_title)
    enough_title_context = min(len(a_tokens), len(b_tokens)) >= 5
    substantial_description = min(len(a.description), len(b.description)) >= 80

    if (
        a.content_hash == b.content_hash
        and (enough_title_context or substantial_description)
        and _within_window(a, b, DEFAULT_EXACT_TIME_WINDOW)
    ):
        return "same normalized content"

    if (
        a.normalized_title == b.normalized_title
        and enough_title_context
        and _within_window(a, b, DEFAULT_EXACT_TIME_WINDOW)
    ):
        return "same normalized title"

    # Fuzzy merging deliberately requires long titles, a high threshold, and a
    # short publication window. Borderline items stay separate.
    if (
        min(len(a_tokens), len(b_tokens)) >= 6
        and title_similarity(a.normalized_title, b.normalized_title) >= threshold
        and _within_window(a, b, DEFAULT_FUZZY_TIME_WINDOW)
    ):
        return "very similar title in the same time window"
    return None


def is_probable_duplicate(
    a: NormalizedItem,
    b: NormalizedItem,
    *,
    threshold: float = DEFAULT_TITLE_SIMILARITY_THRESHOLD,
) -> bool:
    """Return true only when deterministic evidence is strong enough to merge."""

    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    return duplicate_reason(a, b, threshold=threshold) is not None


def find_duplicate(
    candidate: NormalizedItem,
    existing: Iterable[StoredMaterial],
    *,
    threshold: float = DEFAULT_TITLE_SIMILARITY_THRESHOLD,
) -> StoredMaterial | None:
    """Find the strongest existing duplicate, independent of iteration order."""

    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")

    best: StoredMaterial | None = None
    best_strength = -1.0
    for material in existing:
        reason = duplicate_reason(candidate, material.item, threshold=threshold)
        if reason is None:
            continue
        strengths = {
            "same source and external id": 1.0,
            "same canonical URL": 0.99,
            "same normalized content": 0.98,
            "same normalized title": 0.97,
            "very similar title in the same time window": title_similarity(
                candidate.normalized_title, material.item.normalized_title
            ),
        }
        strength = strengths[reason]
        if strength > best_strength:
            best = material
            best_strength = strength
    return best
