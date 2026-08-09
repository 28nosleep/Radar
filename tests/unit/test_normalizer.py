from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from f117.domain import Category, CollectedItem
from f117.pipeline.normalizer import clean_html, normalize_item, normalize_url


def _collected(**updates: object) -> CollectedItem:
    values: dict[str, object] = {
        "external_id": " entry-1 ",
        "source_key": "example",
        "source_name": "Example Feed",
        "source_reputation": 0.8,
        "title": "A useful AI story",
        "url": "https://example.com/story",
        "published_at": datetime(2026, 8, 6, 12, tzinfo=UTC),
        "collected_at": datetime(2026, 8, 6, 13, tzinfo=UTC),
        "description": "Short summary",
        "source_categories": [Category.AI],
    }
    values.update(updates)
    return CollectedItem.model_validate(values)


def test_normalize_url_removes_only_known_tracking_parameters() -> None:
    value = (
        "HTTPS://Example.COM:443/story?"
        "id=7&utm_source=rss&token=a%2Fb&ref=front&FBCLID=tracking#comments"
    )

    assert normalize_url(value) == ("https://example.com/story?id=7&token=a%2Fb&ref=front")


def test_normalize_url_preserves_path_case_trailing_slash_and_query_order() -> None:
    value = "http://EXAMPLE.com:80/News/Item/?b=2&a=1&a=3"

    assert normalize_url(value) == "http://example.com/News/Item/?b=2&a=1&a=3"


@pytest.mark.parametrize(
    "url",
    [
        "http://www.github.com/openai/gpt/",
        "https://github.com/openai/gpt/?utm_source=rss",
    ],
)
def test_normalize_url_collapses_known_safe_aliases(url: str) -> None:
    assert normalize_url(url) == "https://github.com/openai/gpt"


def test_clean_html_removes_markup_and_non_content_elements() -> None:
    value = "<p>Hello&nbsp;<strong>world</strong>.</p><script>bad()</script><p>Next</p>"

    assert clean_html(value) == "Hello world. Next"


def test_normalize_item_cleans_text_converts_dates_and_carries_source_labels() -> None:
    moscow_time = timezone(timedelta(hours=3))
    item = _collected(
        title=" <b>New&nbsp;robot</b>   demo ",
        description="<p>Walks<br>outside</p><style>hidden</style>",
        author=" <span>Ada&nbsp;Lovelace</span> ",
        published_at=datetime(2026, 8, 6, 12, tzinfo=moscow_time),
        collected_at=datetime(2026, 8, 6, 10),
        source_categories=[Category.ROBOTICS],
    )

    result = normalize_item(item)

    assert result.title == "New robot demo"
    assert result.description == "Walks outside"
    assert result.author == "Ada Lovelace"
    assert result.published_at == datetime(2026, 8, 6, 9, tzinfo=UTC)
    assert result.collected_at == datetime(2026, 8, 6, 10, tzinfo=UTC)
    assert result.source_categories == [Category.ROBOTICS]
    assert result.categories == []
    assert result.external_id == "entry-1"
    assert len(result.content_hash) == 64


def test_missing_publication_date_falls_back_to_collection_time() -> None:
    result = normalize_item(
        _collected(
            published_at=None,
            collected_at=datetime(2026, 8, 6, 10),
        )
    )

    assert result.published_at == datetime(2026, 8, 6, 10, tzinfo=UTC)


@pytest.mark.parametrize(
    "url",
    ["", "example.com/story", "ftp://example.com/story", "https:///missing-host"],
)
def test_invalid_or_non_http_url_is_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        normalize_url(url)
