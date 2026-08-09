from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from f117.domain import Category, MetricSnapshot, NormalizedItem, StoredMaterial
from f117.pipeline.discovery import DiscoveryConfig, assess_discovery


def _config() -> DiscoveryConfig:
    return DiscoveryConfig(35, 15, 20, 15, 15, 25, 10, 2000)


def _material(*, mentions: int = 1, stars: float = 300) -> StoredMaterial:
    now = datetime.now(UTC)
    return StoredMaterial(
        id=uuid4(),
        independent_mentions=mentions,
        item=NormalizedItem(
            external_id="1",
            source_key="github",
            source_name="GitHub",
            source_reputation=0.9,
            title="New robotics project",
            url="https://example.com/project",
            canonical_url="https://example.com/project",
            published_at=now - timedelta(hours=6),
            collected_at=now,
            categories=[Category.OPEN_SOURCE],
            popularity={"github_stars": stars},
            content_hash="a" * 64,
            normalized_title="new robotics project",
        ),
    )


def test_discovery_rewards_substantial_growth_and_cross_source_mentions() -> None:
    now = datetime.now(UTC)
    assessment = assess_discovery(
        _material(mentions=3, stars=900),
        [
            MetricSnapshot(captured_at=now - timedelta(hours=6), metrics={"github_stars": 150}),
            MetricSnapshot(captured_at=now, metrics={"github_stars": 900}),
        ],
        now=now,
        config=_config(),
    )

    assert assessment.score >= 55
    assert any("independent sources: 3" in reason for reason in assessment.reasons)


def test_tiny_baseline_does_not_create_false_discovery_signal() -> None:
    now = datetime.now(UTC)
    assessment = assess_discovery(
        _material(stars=3),
        [
            MetricSnapshot(captured_at=now - timedelta(hours=2), metrics={"github_stars": 1}),
            MetricSnapshot(captured_at=now, metrics={"github_stars": 3}),
        ],
        now=now,
        config=_config(),
    )

    assert assessment.score < 40
    assert assessment.reasons == []


def test_hidden_gem_requires_early_growth_with_modest_absolute_popularity() -> None:
    now = datetime.now(UTC)
    assessment = assess_discovery(
        _material(stars=300),
        [
            MetricSnapshot(captured_at=now - timedelta(hours=4), metrics={"github_stars": 50}),
            MetricSnapshot(captured_at=now, metrics={"github_stars": 300}),
        ],
        now=now,
        config=_config(),
    )

    assert assessment.hidden_gem is True


def test_acceleration_requires_three_snapshots() -> None:
    now = datetime.now(UTC)
    two = assess_discovery(
        _material(stars=300),
        [
            MetricSnapshot(captured_at=now - timedelta(hours=4), metrics={"github_stars": 100}),
            MetricSnapshot(captured_at=now, metrics={"github_stars": 300}),
        ],
        now=now,
        config=_config(),
    )
    three = assess_discovery(
        _material(stars=700),
        [
            MetricSnapshot(captured_at=now - timedelta(hours=6), metrics={"github_stars": 100}),
            MetricSnapshot(captured_at=now - timedelta(hours=3), metrics={"github_stars": 200}),
            MetricSnapshot(captured_at=now, metrics={"github_stars": 700}),
        ],
        now=now,
        config=_config(),
    )

    assert three.score > two.score


@pytest.mark.parametrize(
    ("metric", "values", "hours", "expects_growth"),
    [
        ("github_stars", [1, 10], [1], False),
        ("github_stars", [0, 500], [1], True),
        ("github_stars", [100, 500, 100], [1, 1], False),
        ("github_stars", [100, 200], [1], True),
        ("youtube_views", [100, 200], [1], False),
    ],
)
def test_discovery_edge_cases_use_metric_specific_floors(
    metric: str, values: list[float], hours: list[int], expects_growth: bool
) -> None:
    now = datetime.now(UTC)
    captured = [now - timedelta(hours=sum(hours))]
    for interval in hours:
        captured.append(captured[-1] + timedelta(hours=interval))
    assessment = assess_discovery(
        _material(stars=values[-1]),
        [
            MetricSnapshot(captured_at=at, metrics={metric: value})
            for at, value in zip(captured, values, strict=True)
        ],
        now=now,
        config=_config(),
    )

    assert any(reason.startswith("growth:") for reason in assessment.reasons) is expects_growth


def test_same_percentage_growth_is_lower_over_longer_interval() -> None:
    now = datetime.now(UTC)
    fast = assess_discovery(
        _material(stars=200),
        [
            MetricSnapshot(captured_at=now - timedelta(hours=1), metrics={"github_stars": 100}),
            MetricSnapshot(captured_at=now, metrics={"github_stars": 200}),
        ],
        now=now,
        config=_config(),
    )
    slow = assess_discovery(
        _material(stars=200),
        [
            MetricSnapshot(captured_at=now - timedelta(hours=23), metrics={"github_stars": 100}),
            MetricSnapshot(captured_at=now, metrics={"github_stars": 200}),
        ],
        now=now,
        config=_config(),
    )

    assert fast.score > slow.score
