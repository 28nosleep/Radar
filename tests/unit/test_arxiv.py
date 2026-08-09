from __future__ import annotations

from datetime import UTC, datetime

from f117.adapters.arxiv import ArxivCollector
from f117.domain import Category, FeedSource


def _source() -> FeedSource:
    return FeedSource(
        key="arxiv-ai",
        name="arXiv",
        kind="arxiv",
        feed_url="https://export.arxiv.org/api/query",
        reputation=0.95,
        default_categories=[Category.RESEARCH],
        arxiv_categories=["cs.AI"],
    )


def test_entry_normalization_extracts_paper_fields() -> None:
    item = ArxivCollector._entry_to_item(
        _source(),
        {
            "id": "http://arxiv.org/abs/2608.01234v1",
            "title": " A robot paper ",
            "link": "https://arxiv.org/abs/2608.01234",
            "summary": "Paper abstract.",
            "authors": [{"name": "Ada"}, {"name": "Lin"}],
            "published_parsed": (2026, 8, 8, 12, 0, 0, 0, 0, 0),
            "tags": [{"term": "cs.AI"}, {"term": "cs.RO"}],
        },
        datetime(2026, 8, 9, tzinfo=UTC),
    )

    assert item is not None
    assert item.external_id == "2608.01234v1"
    assert item.author == "Ada, Lin"
    assert item.description == "Paper abstract."
    assert item.raw["arxiv_categories"] == ["cs.AI", "cs.RO"]


def test_entry_normalization_rejects_incomplete_paper() -> None:
    assert (
        ArxivCollector._entry_to_item(_source(), {"title": "Only title"}, datetime.now(UTC)) is None
    )
