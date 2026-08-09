"""Small, explainable early-signal scoring built on stored metric snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from math import log1p

from f117.domain import MetricSnapshot, StoredMaterial

_METRIC_KEYS = (
    "github_stars",
    "reddit_upvotes",
    "youtube_views",
    "points",
    "views",
    "stars",
    "upvotes",
)


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    growth_weight: float
    acceleration_weight: float
    diversity_weight: float
    novelty_weight: float
    freshness_weight: float
    min_baseline: float
    min_growth_absolute: float
    hidden_gem_max_popularity: float

    @property
    def weight_total(self) -> float:
        return sum(
            (
                self.growth_weight,
                self.acceleration_weight,
                self.diversity_weight,
                self.novelty_weight,
                self.freshness_weight,
            )
        )


@dataclass(frozen=True, slots=True)
class DiscoveryAssessment:
    score: float
    reasons: list[str]
    hidden_gem: bool


def assess_discovery(
    material: StoredMaterial,
    history: list[MetricSnapshot],
    *,
    now: datetime,
    config: DiscoveryConfig,
) -> DiscoveryAssessment:
    """Score only evidence present in snapshots; weak tiny baselines are damped."""

    growth, acceleration, growth_reason = _growth_signals(history, config)
    diversity = min(1.0, max(0, material.independent_mentions - 1) / 3.0)
    age_hours = max(0.0, (now - material.item.published_at).total_seconds() / 3600.0)
    freshness = 2.0 ** (-age_hours / 24.0)
    novelty = 1.0 if age_hours <= 24.0 else max(0.0, 1.0 - (age_hours - 24.0) / 144.0)
    weighted = (
        growth * config.growth_weight
        + acceleration * config.acceleration_weight
        + diversity * config.diversity_weight
        + novelty * config.novelty_weight
        + freshness * config.freshness_weight
    )
    score = round(100.0 * weighted / config.weight_total, 2) if config.weight_total else 0.0
    current = _primary_metric(material.item.popularity)
    hidden_gem = (
        growth >= 0.45
        and freshness >= 0.5
        and current is not None
        and current[1] <= config.hidden_gem_max_popularity
    )
    reasons = [growth_reason] if growth_reason else []
    if diversity:
        reasons.append(f"independent sources: {material.independent_mentions}")
    if hidden_gem:
        reasons.append("hidden gem: early growth with modest absolute popularity")
    return DiscoveryAssessment(score=score, reasons=reasons, hidden_gem=hidden_gem)


def _growth_signals(
    history: list[MetricSnapshot], config: DiscoveryConfig
) -> tuple[float, float, str]:
    if len(history) < 2:
        return 0.0, 0.0, ""
    pairs: list[tuple[float, float, float, float]] = []
    for left, right in pairwise(history):
        primary = _shared_primary(left.metrics, right.metrics)
        if primary is None:
            continue
        before, after = primary
        hours = (right.captured_at - left.captured_at).total_seconds() / 3600.0
        if hours > 0 and after >= before:
            pairs.append((before, after, hours, (after - before) / hours))
    if not pairs:
        return 0.0, 0.0, ""
    before, after, hours, rate = pairs[-1]
    absolute = after - before
    if before < config.min_baseline or absolute < config.min_growth_absolute:
        return 0.0, 0.0, ""
    percent = absolute / before * 100.0
    baseline_gate = min(1.0, before / config.min_baseline)
    absolute_gate = min(1.0, absolute / max(config.min_growth_absolute * 10.0, 1.0))
    growth = min(1.0, log1p(percent) / log1p(300.0)) * baseline_gate * absolute_gate
    acceleration = 0.0
    if len(pairs) >= 2 and pairs[-2][3] > 0:
        acceleration = min(1.0, max(0.0, rate / pairs[-2][3] - 1.0))
    return growth, acceleration, f"growth: +{percent:.0f}% over {hours:.1f}h"


def _primary_metric(metrics: dict[str, float]) -> tuple[str, float] | None:
    for key in _METRIC_KEYS:
        value = metrics.get(key)
        if value is not None:
            return key, float(value)
    return None


def _shared_primary(left: dict[str, float], right: dict[str, float]) -> tuple[float, float] | None:
    for key in _METRIC_KEYS:
        if key in left and key in right:
            return float(left[key]), float(right[key])
    return None
