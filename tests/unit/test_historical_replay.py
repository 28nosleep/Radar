from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from f117.config import Settings
from f117.domain import Category, MetricSnapshot, NormalizedItem, StoredMaterial
from f117.services.historical_replay import run_deterministic_historical_replay

AS_OF = datetime(2026, 8, 5, 12, tzinfo=UTC)


def _material(
    *,
    title: str = "OpenAI releases a major new AI model",
    source_key: str = "hacker-news",
    published_at: datetime = AS_OF - timedelta(hours=2),
    collected_at: datetime = AS_OF - timedelta(hours=1),
) -> StoredMaterial:
    item = NormalizedItem(
        external_id=str(uuid4()),
        source_key=source_key,
        source_name="Historical source",
        source_reputation=0.9,
        title=title,
        url="https://example.com/item",
        canonical_url="https://example.com/item",
        published_at=published_at,
        collected_at=collected_at,
        description="A concrete major release with immediate public availability and benchmarks.",
        source_categories=[Category.AI],
        categories=[Category.AI],
        popularity={},
        content_hash="a" * 64,
        normalized_title=title.casefold(),
    )
    return StoredMaterial(id=uuid4(), item=item)


def test_historical_as_of_rejects_future_observations_and_publications() -> None:
    future_observation = _material(collected_at=AS_OF + timedelta(seconds=1))
    future_publication = _material(published_at=AS_OF + timedelta(seconds=1))

    result = run_deterministic_historical_replay(
        [future_observation, future_publication],
        {},
        as_of=AS_OF,
        settings=Settings(_env_file=None),
    )

    assert result.rejected_future_observation_count == 1
    assert result.rejected_freshness_count == 1
    assert not result.finalists


def test_future_metric_snapshots_are_ignored() -> None:
    material = _material(title="OpenAI releases a small AI update")
    future = MetricSnapshot(
        captured_at=AS_OF + timedelta(hours=1),
        metrics={"hn_points": 1_000_000, "hn_comments": 50_000},
    )

    with_future = run_deterministic_historical_replay(
        [material],
        {material.id: [future]},
        as_of=AS_OF,
        settings=Settings(_env_file=None),
    )
    without_future = run_deterministic_historical_replay(
        [material],
        {},
        as_of=AS_OF,
        settings=Settings(_env_file=None),
    )

    assert with_future == without_future


def test_replay_is_in_memory_and_ignores_delivery_and_enrichment_state() -> None:
    material = _material().model_copy(update={"delivered_at": AS_OF - timedelta(minutes=1)})
    original = material.model_dump(mode="python")

    result = run_deterministic_historical_replay(
        [material],
        {},
        as_of=AS_OF,
        settings=Settings(_env_file=None),
    )

    assert result.collected_count == 1
    assert material.model_dump(mode="python") == original


def test_replay_decision_has_no_feedback_or_telegram_input() -> None:
    names = run_deterministic_historical_replay.__annotations__

    assert "feedback" not in names
    assert "notifier" not in names
