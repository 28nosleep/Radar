"""Small, explainable early-signal scoring built on stored metric snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from math import log1p

from f117.domain import MetricSnapshot, StoredMaterial


@dataclass(frozen=True, slots=True)
class MetricProfile:
    min_baseline: float
    min_absolute: float
    min_rate_per_hour: float
    saturation_absolute: float
    saturation_rate_per_hour: float


# Metrics are intentionally not interchangeable: a 10-star GitHub jump and a
# 10-view YouTube jump do not mean the same thing. Legacy generic keys remain so
# old rows keep a conservative interpretation after the migration-free upgrade.
METRIC_PROFILES: dict[str, MetricProfile] = {
    "github_stars": MetricProfile(25, 10, 2, 250, 50),
    "youtube_views": MetricProfile(1_000, 250, 50, 50_000, 10_000),
    "hn_points": MetricProfile(20, 10, 3, 250, 50),
    "hn_comments": MetricProfile(5, 3, 1, 75, 15),
    "reddit_upvotes": MetricProfile(20, 10, 3, 250, 50),
    "reddit_comments": MetricProfile(5, 3, 1, 75, 15),
    "points": MetricProfile(20, 10, 3, 250, 50),
    "comments": MetricProfile(5, 3, 1, 75, 15),
    "stars": MetricProfile(25, 10, 2, 250, 50),
    "youtube_likes": MetricProfile(50, 20, 5, 2_000, 300),
}


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


@dataclass(frozen=True, slots=True)
class _GrowthSignal:
    value: float
    rate: float
    reason: str
    corrected: bool = False


def assess_discovery(
    material: StoredMaterial,
    history: list[MetricSnapshot],
    *,
    now: datetime,
    config: DiscoveryConfig,
) -> DiscoveryAssessment:
    """Score the latest metric transition; corrections clear stale rising signals."""

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


def metric_growth_signal(
    key: str,
    before: float,
    after: float,
    hours: float,
    config: DiscoveryConfig,
) -> _GrowthSignal:
    """Return one metric's calibrated latest-transition signal.

    Corrections are not growth. A zero baseline can still be meaningful only with
    a substantial absolute and rate floor; tiny 1→10 changes stay damped.
    """

    if hours <= 0:
        return _GrowthSignal(0.0, 0.0, "")
    if after < before:
        return _GrowthSignal(0.0, 0.0, f"metric correction: {key}", corrected=True)
    absolute = after - before
    profile = METRIC_PROFILES.get(
        key,
        MetricProfile(
            config.min_baseline,
            config.min_growth_absolute,
            max(1.0, config.min_growth_absolute / 4),
            max(1.0, config.min_growth_absolute * 25),
            max(1.0, config.min_growth_absolute * 5),
        ),
    )
    rate = absolute / hours
    if absolute < profile.min_absolute or rate < profile.min_rate_per_hour:
        return _GrowthSignal(0.0, rate, "")
    baseline_gate = min(1.0, before / profile.min_baseline) if before > 0 else 0.55
    # Tiny non-zero baselines should not earn a percentage spike by themselves.
    if 0 < before < profile.min_baseline:
        baseline_gate *= before / profile.min_baseline
    absolute_signal = min(1.0, absolute / profile.saturation_absolute)
    rate_signal = min(1.0, rate / profile.saturation_rate_per_hour)
    percent_signal = min(1.0, log1p(absolute / max(before, 1.0) * 100) / log1p(300.0))
    value = min(1.0, (0.45 * absolute_signal + 0.40 * rate_signal + 0.15 * percent_signal))
    value *= max(0.2, baseline_gate)
    return _GrowthSignal(
        round(value, 6),
        rate,
        f"growth: {key} +{absolute:.0f} over {hours:.1f}h ({rate:.1f}/h)",
    )


def _growth_signals(
    history: list[MetricSnapshot], config: DiscoveryConfig
) -> tuple[float, float, str]:
    if len(history) < 2:
        return 0.0, 0.0, ""
    latest_left, latest_right = history[-2], history[-1]
    hours = (latest_right.captured_at - latest_left.captured_at).total_seconds() / 3600.0
    latest = _best_pair_signal(latest_left.metrics, latest_right.metrics, hours, config)
    if latest.corrected:
        return 0.0, 0.0, latest.reason
    if latest.value == 0:
        return 0.0, 0.0, ""

    previous_rates: list[float] = []
    for left, right in pairwise(history[:-1]):
        pair_hours = (right.captured_at - left.captured_at).total_seconds() / 3600.0
        signal = _best_pair_signal(left.metrics, right.metrics, pair_hours, config)
        if signal.value > 0:
            previous_rates.append(signal.rate)
    acceleration = 0.0
    if previous_rates and previous_rates[-1] > 0:
        acceleration = min(1.0, max(0.0, latest.rate / previous_rates[-1] - 1.0))
    return latest.value, acceleration, latest.reason


def _best_pair_signal(
    left: dict[str, float], right: dict[str, float], hours: float, config: DiscoveryConfig
) -> _GrowthSignal:
    signals = [
        metric_growth_signal(key, float(left[key]), float(right[key]), hours, config)
        for key in METRIC_PROFILES
        if key in left and key in right
    ]
    if not signals:
        return _GrowthSignal(0.0, 0.0, "")
    corrections = [signal for signal in signals if signal.corrected]
    if corrections:
        return corrections[0]
    return max(signals, key=lambda signal: signal.value)


def _primary_metric(metrics: dict[str, float]) -> tuple[str, float] | None:
    for key in METRIC_PROFILES:
        value = metrics.get(key)
        if value is not None:
            return key, float(value)
    return None
