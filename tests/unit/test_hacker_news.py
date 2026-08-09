from __future__ import annotations

from datetime import UTC, datetime

from f117.adapters.hacker_news import HackerNewsCollector
from f117.domain import Category, FeedSource


def _source() -> FeedSource:
    return FeedSource(
        key="hacker-news",
        name="Hacker News",
        kind="hacker_news",
        feed_url="https://hacker-news.firebaseio.com/v0",
        collection="best",
        item_limit=2,
        reputation=0.8,
        default_categories=[Category.AI],
    )


def test_story_normalization_carries_popularity_and_fallback_url() -> None:
    item = HackerNewsCollector._story_to_item(
        _source(),
        {
            "id": 42,
            "type": "story",
            "title": "A new open-source LLM",
            "by": "ada",
            "time": 1_700_000_000,
            "score": 321,
            "descendants": 44,
        },
        datetime(2026, 8, 9, tzinfo=UTC),
    )

    assert item is not None
    assert item.external_id == "42"
    assert item.url == "https://news.ycombinator.com/item?id=42"
    assert item.popularity == {"points": 321.0, "comments": 44.0}
    assert item.author == "ada"


def test_story_normalization_skips_deleted_and_non_story_items() -> None:
    timestamp = datetime.now(UTC)
    assert (
        HackerNewsCollector._story_to_item(_source(), {"id": 1, "type": "comment"}, timestamp)
        is None
    )
    assert (
        HackerNewsCollector._story_to_item(
            _source(), {"id": 2, "type": "story", "title": "gone", "deleted": True}, timestamp
        )
        is None
    )
