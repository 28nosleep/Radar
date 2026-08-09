from __future__ import annotations

from typing import Any, cast

import pytest

from f117.adapters.rss import RSSCollector, RSSFetchError
from f117.domain import Category, FeedSource

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example AI</title>
  <entry>
    <title>New robot learns a difficult task</title>
    <id>entry-1</id>
    <link href="https://example.com/robot?utm_source=rss" />
    <updated>2026-08-06T09:30:00Z</updated>
    <summary type="html">&lt;p&gt;A useful &lt;b&gt;summary&lt;/b&gt;.&lt;/p&gt;</summary>
    <author><name>Research Team</name></author>
  </entry>
</feed>
"""


class _Content:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def iter_chunked(self, _: int) -> Any:
        midpoint = len(self.body) // 2
        for chunk in (self.body[:midpoint], self.body[midpoint:]):
            if chunk:
                yield chunk


class _Response:
    def __init__(
        self,
        body: bytes = b"",
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        content_length: int | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.content_length = len(body) if content_length is None else content_length
        self.content = _Content(body)

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.request_headers: list[dict[str, str]] = []

    def get(self, _: str, *, headers: dict[str, str]) -> _Response:
        self.request_headers.append(headers)
        return self.responses.pop(0)


def _source() -> FeedSource:
    return FeedSource(
        key="example-ai",
        name="Example AI",
        feed_url="https://example.com/feed",
        reputation=0.9,
        default_categories=[Category.AI],
    )


@pytest.mark.asyncio
async def test_fetch_parses_atom_and_uses_conditional_request_headers() -> None:
    session = _Session(
        [
            _Response(
                ATOM,
                headers={
                    "ETag": '"feed-v1"',
                    "Last-Modified": "Thu, 06 Aug 2026 10:00:00 GMT",
                },
            ),
            _Response(status=304, headers={"ETag": '"feed-v1"'}),
        ]
    )
    collector = RSSCollector(timeout_seconds=2)

    first = await collector.fetch(_source(), session=cast(Any, session))
    second = await collector.fetch(_source(), etag=first.etag, session=cast(Any, session))

    assert len(first.items) == 1
    assert first.items[0].external_id == "entry-1"
    assert first.items[0].source_categories == [Category.AI]
    assert first.items[0].published_at is not None
    assert first.items[0].author == "Research Team"
    assert first.etag == '"feed-v1"'
    assert second.not_modified is True
    assert second.items == []
    assert session.request_headers[0].get("If-None-Match") is None
    assert session.request_headers[1]["If-None-Match"] == '"feed-v1"'


@pytest.mark.asyncio
async def test_fetch_rejects_an_oversized_response() -> None:
    session = _Session([_Response(b"x" * 256)])
    collector = RSSCollector(timeout_seconds=2, max_response_bytes=128)

    with pytest.raises(RSSFetchError, match=r"too large|exceeded"):
        await collector.fetch(_source(), session=cast(Any, session))


@pytest.mark.asyncio
async def test_fetch_reports_malformed_feed_without_entries() -> None:
    session = _Session([_Response(b"<rss><broken>")])

    with pytest.raises(RSSFetchError, match="Malformed feed"):
        await RSSCollector(timeout_seconds=2).fetch(_source(), session=cast(Any, session))


@pytest.mark.asyncio
async def test_item_limit_marks_feed_partial_so_checkpoint_can_stay_put() -> None:
    two_entries = ATOM.replace(
        b"</feed>",
        b"""<entry><title>Second AI item</title><id>entry-2</id>
        <link href=\"https://example.com/second\" /><updated>2026-08-06T10:30:00Z</updated>
        </entry></feed>""",
    )
    source = _source().model_copy(update={"item_limit": 1})
    result = await RSSCollector(timeout_seconds=2).fetch(
        source, session=cast(Any, _Session([_Response(two_entries)]))
    )

    assert [item.external_id for item in result.items] == ["entry-1"]
    assert result.partial is True
