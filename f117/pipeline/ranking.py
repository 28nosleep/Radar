"""Interpretable rule-based ranking for one-owner editorial selection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from math import log1p

from f117.domain import Category, RankedMaterial, StoredMaterial


@dataclass(frozen=True, slots=True)
class RankingSignals:
    """Optional normalized (0..1) signals supplied by later discovery stages."""

    popularity: float | None = None
    growth: float | None = None
    novelty: float | None = None
    unusualness: float | None = None
    topic_fit: float | None = None

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"{field.name} must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class RankingConfig:
    freshness_weight: float = 20.0
    reputation_weight: float = 15.0
    mentions_weight: float = 15.0
    popularity_weight: float = 15.0
    growth_weight: float = 10.0
    novelty_weight: float = 10.0
    topic_fit_weight: float = 10.0
    unusualness_weight: float = 5.0
    freshness_half_life_hours: float = 36.0
    full_mentions: int = 5
    default_novelty: float = 0.45
    default_unusualness: float = 0.20
    funny_unusualness: float = 0.70
    wtf_unusualness: float = 0.90
    default_topic_fit: float = 0.75
    other_topic_fit: float = 0.08
    other_popularity_cap: float = 0.20
    other_growth_cap: float = 0.20

    def __post_init__(self) -> None:
        weights = self.weights
        if any(weight < 0 for weight in weights.values()) or sum(weights.values()) <= 0:
            raise ValueError("ranking weights must be non-negative with a positive sum")
        if self.freshness_half_life_hours <= 0:
            raise ValueError("freshness_half_life_hours must be positive")
        if self.full_mentions < 2:
            raise ValueError("full_mentions must be at least 2")
        for name in (
            "default_novelty",
            "default_unusualness",
            "funny_unusualness",
            "wtf_unusualness",
            "default_topic_fit",
            "other_topic_fit",
            "other_popularity_cap",
            "other_growth_cap",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")

    @property
    def weights(self) -> dict[str, float]:
        return {
            "freshness": self.freshness_weight,
            "reputation": self.reputation_weight,
            "mentions": self.mentions_weight,
            "popularity": self.popularity_weight,
            "growth": self.growth_weight,
            "novelty": self.novelty_weight,
            "topic_fit": self.topic_fit_weight,
            "unusualness": self.unusualness_weight,
        }


_POPULARITY_TARGETS: Mapping[str, float] = {
    "comments": 500.0,
    "github_stars": 5_000.0,
    "hn_comments": 500.0,
    "hn_points": 1_000.0,
    "likes": 100_000.0,
    "points": 1_000.0,
    "reddit_comments": 500.0,
    "reddit_upvotes": 2_000.0,
    "score": 2_000.0,
    "stars": 5_000.0,
    "upvotes": 2_000.0,
    "views": 1_000_000.0,
    "youtube_views": 1_000_000.0,
}
_GROWTH_TARGETS: Mapping[str, float] = {
    "comments_per_hour": 50.0,
    "github_stars_per_hour": 50.0,
    "hn_comments_per_hour": 50.0,
    "hn_points_per_hour": 50.0,
    "stars_per_hour": 50.0,
    "upvotes_per_hour": 50.0,
    "views_per_hour": 10_000.0,
    "youtube_views_per_hour": 10_000.0,
    "growth_per_hour": 25.0,
    "reddit_upvotes_per_hour": 50.0,
    "reddit_comments_per_hour": 15.0,
}


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _log_normalize(value: float, target: float) -> float:
    if value <= 0:
        return 0.0
    return _clamp(log1p(value) / log1p(target))


def _metric_signal(
    metrics: Mapping[str, float],
    *,
    direct_keys: tuple[str, ...],
    targets: Mapping[str, float],
) -> float:
    values: list[float] = []
    for key in direct_keys:
        raw_value = metrics.get(key)
        if raw_value is not None and 0.0 <= raw_value <= 1.0:
            values.append(float(raw_value))
    for key, target in targets.items():
        raw_value = metrics.get(key)
        if raw_value is not None:
            values.append(_log_normalize(float(raw_value), target))
    return max(values, default=0.0)


def _freshness_signal(published_at: datetime, now: datetime, half_life: float) -> float:
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    else:
        published_at = published_at.astimezone(UTC)
    age_hours = max(0.0, (now - published_at).total_seconds() / 3600.0)
    return float(2.0 ** (-age_hours / half_life))


def _mentions_signal(mentions: int, full_mentions: int) -> float:
    additional_mentions = max(0, mentions - 1)
    return _clamp(log1p(additional_mentions) / log1p(full_mentions - 1))


def _default_topic_fit(categories: list[Category], config: RankingConfig) -> float:
    if _is_other_only(categories):
        return config.other_topic_fit
    return config.default_topic_fit


def _is_other_only(categories: list[Category]) -> bool:
    return not categories or set(categories) == {Category.OTHER}


def _default_unusualness(categories: list[Category], config: RankingConfig) -> float:
    if Category.WTF in categories:
        return config.wtf_unusualness
    if Category.FUNNY in categories:
        return config.funny_unusualness
    return config.default_unusualness


def _signal_or_default(value: float | None, default: float) -> float:
    return default if value is None else value


def score_material(
    material: StoredMaterial,
    *,
    now: datetime | None = None,
    config: RankingConfig | None = None,
    signals: RankingSignals | None = None,
) -> RankedMaterial:
    """Calculate a stable 0..100 score and human-readable component reasons."""

    config = config or RankingConfig()
    signals = signals or RankingSignals()
    evaluation_time = now or datetime.now(UTC)
    if evaluation_time.tzinfo is None:
        evaluation_time = evaluation_time.replace(tzinfo=UTC)
    else:
        evaluation_time = evaluation_time.astimezone(UTC)

    popularity = _signal_or_default(
        signals.popularity,
        _metric_signal(
            material.item.popularity,
            direct_keys=("popularity_score", "popularity_normalized"),
            targets=_POPULARITY_TARGETS,
        ),
    )
    if _is_other_only(material.item.categories):
        popularity = min(popularity, config.other_popularity_cap)
    growth = _signal_or_default(
        signals.growth,
        _metric_signal(
            material.item.popularity,
            direct_keys=("growth_score", "growth_normalized"),
            targets=_GROWTH_TARGETS,
        ),
    )
    if _is_other_only(material.item.categories):
        growth = min(growth, config.other_growth_cap)
    factors = {
        "freshness": _freshness_signal(
            material.item.published_at,
            evaluation_time,
            config.freshness_half_life_hours,
        ),
        "reputation": _clamp(material.item.source_reputation),
        "mentions": _mentions_signal(material.independent_mentions, config.full_mentions),
        "popularity": popularity,
        "growth": growth,
        "novelty": _signal_or_default(signals.novelty, config.default_novelty),
        "topic_fit": _signal_or_default(
            signals.topic_fit,
            _default_topic_fit(material.item.categories, config),
        ),
        "unusualness": _signal_or_default(
            signals.unusualness,
            _default_unusualness(material.item.categories, config),
        ),
    }

    total_weight = sum(config.weights.values())
    scale = 100.0 / total_weight
    contributions = {
        name: _clamp(factor) * config.weights[name] * scale for name, factor in factors.items()
    }
    score = round(_clamp(sum(contributions.values()) / 100.0) * 100.0, 2)
    reasons = [
        f"{name}: {contributions[name]:.1f}/{config.weights[name] * scale:.1f} "
        f"(signal={factors[name]:.2f})"
        for name in config.weights
    ]

    return RankedMaterial(
        material_id=material.id,
        title=material.item.title,
        url=material.item.canonical_url,
        source_name=material.item.source_name,
        published_at=material.item.published_at,
        description=material.item.description,
        categories=list(material.item.categories),
        popularity=dict(material.item.popularity),
        qualitative_signals=list(material.item.qualitative_signals),
        media_type=material.item.media_type,
        media_url=material.item.media_url,
        thumbnail_url=material.item.thumbnail_url,
        media_source=material.item.media_source,
        independent_mentions=material.independent_mentions,
        score=score,
        score_reasons=reasons,
    )
