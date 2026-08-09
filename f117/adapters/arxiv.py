from __future__ import annotations

import asyncio
import calendar
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import aiohttp
import feedparser

from f117.adapters.rss import FeedFetchResult
from f117.domain import CollectedItem, FeedSource


class ArxivFetchError(RuntimeError):
    pass


class ArxivCollector:
    """Fetch recent papers from the public arXiv Atom API."""

    def __init__(
        self, *, timeout_seconds: float = 20.0, max_response_bytes: int = 5_000_000, user_agent: str
    ) -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.max_response_bytes = max_response_bytes
        self.headers = {"User-Agent": user_agent, "Accept": "application/atom+xml, application/xml"}

    async def fetch(
        self,
        source: FeedSource,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> FeedFetchResult:
        del etag, last_modified
        if not source.arxiv_categories:
            raise ArxivFetchError(f"{source.key} must declare arxiv_categories")
        owns_session = session is None
        if session is None:
            session = aiohttp.ClientSession(timeout=self.timeout, headers=self.headers)
        try:
            query = " OR ".join(f"cat:{category}" for category in source.arxiv_categories)
            params = urlencode(
                {
                    "search_query": query,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                    "max_results": source.item_limit,
                }
            )
            async with session.get(f"{source.feed_url}?{params}", headers=self.headers) as response:
                if response.status != 200:
                    raise ArxivFetchError(f"{source.key} returned HTTP {response.status}")
                if (
                    response.content_length is not None
                    and response.content_length > self.max_response_bytes
                ):
                    raise ArxivFetchError(f"{source.key} response is too large")
                body = await self._read_bounded(response)
            parsed = await asyncio.to_thread(feedparser.parse, body)
            if parsed.bozo and not parsed.entries:
                raise ArxivFetchError(f"Malformed arXiv feed: {parsed.bozo_exception}")
            collected_at = datetime.now(UTC)
            return FeedFetchResult(
                items=[
                    item
                    for entry in parsed.entries
                    if (item := self._entry_to_item(source, entry, collected_at)) is not None
                ],
                etag=None,
                last_modified=None,
            )
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ArxivFetchError(f"Failed to fetch {source.key}: {exc}") from exc
        finally:
            if owns_session:
                await session.close()

    async def _read_bounded(self, response: aiohttp.ClientResponse) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.content.iter_chunked(64 * 1024):
            size += len(chunk)
            if size > self.max_response_bytes:
                raise ArxivFetchError(f"arXiv response exceeded {self.max_response_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _entry_to_item(
        source: FeedSource, entry: Any, collected_at: datetime
    ) -> CollectedItem | None:
        title = str(entry.get("title") or "").strip()
        url = str(entry.get("link") or entry.get("id") or "").strip()
        external_id = str(entry.get("id") or "").rsplit("/", maxsplit=1)[-1].strip()
        if not title or not url or not external_id:
            return None
        authors = ", ".join(
            str(author.get("name") or "").strip()
            for author in entry.get("authors", [])
            if author.get("name")
        )
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        published_at = (
            datetime.fromtimestamp(calendar.timegm(published), tz=UTC) if published else None
        )
        tags = [str(tag.get("term")) for tag in entry.get("tags", []) if tag.get("term")]
        return CollectedItem(
            external_id=external_id,
            source_key=source.key,
            source_name=source.name,
            source_reputation=source.reputation,
            title=title,
            url=url,
            published_at=published_at,
            collected_at=collected_at,
            description=str(entry.get("summary") or ""),
            author=authors or None,
            source_categories=source.default_categories,
            popularity={},
            raw={"arxiv_categories": tags},
        )
