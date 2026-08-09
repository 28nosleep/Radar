from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from f117.domain import Category, NormalizedItem, StoredMaterial
from f117.pipeline.ranking import RankingSignals, score_material

NOW = datetime(2026, 8, 6, 12, tzinfo=UTC)


def _material(**updates: object) -> StoredMaterial:
    item_updates = dict(updates.pop("item_updates", {}))
    item_values: dict[str, object] = {
        "external_id": "1",
        "source_key": "example",
        "source_name": "Example",
        "source_reputation": 0.5,
        "title": "A new AI release",
        "url": "https://example.com/item",
        "canonical_url": "https://example.com/item",
        "published_at": NOW,
        "collected_at": NOW,
        "description": "Summary",
        "source_categories": [],
        "categories": [Category.AI],
        "popularity": {},
        "content_hash": "a" * 64,
        "normalized_title": "a new ai release",
    }
    item_values.update(item_updates)
    values: dict[str, object] = {
        "id": UUID(int=1),
        "item": NormalizedItem.model_validate(item_values),
        "independent_mentions": 1,
    }
    values.update(updates)
    return StoredMaterial.model_validate(values)


def test_score_is_bounded_and_every_component_is_explained() -> None:
    result = score_material(
        _material(independent_mentions=20),
        now=NOW,
        signals=RankingSignals(
            popularity=1.0,
            growth=1.0,
            novelty=1.0,
            unusualness=1.0,
            topic_fit=1.0,
        ),
    )

    assert 0.0 <= result.score <= 100.0
    assert result.score == pytest.approx(92.5)
    assert [reason.partition(":")[0] for reason in result.score_reasons] == [
        "freshness",
        "reputation",
        "mentions",
        "popularity",
        "growth",
        "novelty",
        "topic_fit",
        "unusualness",
    ]


def test_fresh_material_scores_higher_than_old_material() -> None:
    fresh = score_material(_material(), now=NOW)
    old = score_material(
        _material(item_updates={"published_at": NOW - timedelta(days=7)}),
        now=NOW,
    )

    assert fresh.score > old.score


def test_raw_popularity_and_growth_metrics_raise_score() -> None:
    quiet = score_material(_material(), now=NOW)
    viral = score_material(
        _material(
            item_updates={
                "popularity": {
                    "github_stars": 5_000,
                    "github_stars_per_hour": 500,
                }
            }
        ),
        now=NOW,
    )

    assert viral.score == pytest.approx(quiet.score + 25.0)


def test_more_independent_mentions_raise_score_until_saturation() -> None:
    one = score_material(_material(independent_mentions=1), now=NOW)
    five = score_material(_material(independent_mentions=5), now=NOW)
    many = score_material(_material(independent_mentions=50), now=NOW)

    assert five.score > one.score
    assert many.score == five.score


def test_default_topic_and_unusualness_use_categories() -> None:
    other = score_material(
        _material(item_updates={"categories": [Category.OTHER]}),
        now=NOW,
    )
    wtf = score_material(
        _material(item_updates={"categories": [Category.WTF]}),
        now=NOW,
    )

    assert wtf.score > other.score


def test_invalid_explicit_signal_is_rejected() -> None:
    with pytest.raises(ValueError, match="novelty"):
        RankingSignals(novelty=1.1)


def test_popular_irrelevant_hacker_news_item_cannot_win_on_popularity_alone() -> None:
    irrelevant = score_material(
        _material(
            item_updates={
                "source_name": "Hacker News",
                "categories": [Category.OTHER],
                "popularity": {"hn_points": 10_000, "hn_comments": 2_000},
            }
        ),
        now=NOW,
    )
    relevant = score_material(_material(), now=NOW)

    assert relevant.score > irrelevant.score


def test_irrelevant_growth_is_capped_alongside_popularity() -> None:
    relevant = score_material(
        _material(
            item_updates={
                "title": "arXiv: new method for reliable long-context language models",
                "categories": [Category.RESEARCH],
            }
        ),
        now=NOW,
    )
    for title in (
        "reMarkable launches a general-purpose tablet accessory",
        "Old Windows port becomes viral on Hacker News",
    ):
        irrelevant = score_material(
            _material(
                item_updates={
                    "title": title,
                    "source_name": "Hacker News",
                    "categories": [Category.OTHER],
                    "popularity": {"hn_points": 20_000, "hn_points_per_hour": 2_000},
                }
            ),
            now=NOW,
        )
        assert relevant.score > irrelevant.score
