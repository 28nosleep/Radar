from __future__ import annotations

import asyncio
import calendar
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from time import struct_time
from typing import Any

import aiohttp
import feedparser

from f117.domain import CollectedItem, FeedSource


class RSSFetchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FeedFetchResult:
    items: list[CollectedItem]
    etag: str | None
    last_modified: str | None
    not_modified: bool = False


class RSSCollector:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        max_response_bytes: int = 5_000_000,
        user_agent: str = "Radar-Intelligence-Engine/0.1",
    ) -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.max_response_bytes = max_response_bytes
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        }

    async def fetch(
        self,
        source: FeedSource,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> FeedFetchResult:
        owns_session = session is None
        if session is None:
            session = aiohttp.ClientSession(timeout=self.timeout, headers=self.headers)
        try:
            return await self._fetch_with_session(
                session,
                source,
                etag=etag,
                last_modified=last_modified,
            )
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise RSSFetchError(f"Failed to fetch {source.key}: {exc}") from exc
        finally:
            if owns_session:
                await session.close()

    async def _fetch_with_session(
        self,
        session: aiohttp.ClientSession,
        source: FeedSource,
        *,
        etag: str | None,
        last_modified: str | None,
    ) -> FeedFetchResult:
        request_headers: dict[str, str] = dict(self.headers)
        if etag:
            request_headers["If-None-Match"] = etag
        if last_modified:
            request_headers["If-Modified-Since"] = last_modified

        async with session.get(str(source.feed_url), headers=request_headers) as response:
            response_etag = response.headers.get("ETag") or etag
            response_last_modified = response.headers.get("Last-Modified") or last_modified
            if response.status == 304:
                return FeedFetchResult(
                    items=[],
                    etag=response_etag,
                    last_modified=response_last_modified,
                    not_modified=True,
                )
            if response.status != 200:
                raise RSSFetchError(f"{source.key} returned HTTP {response.status}")
            content_length = response.content_length
            if content_length is not None and content_length > self.max_response_bytes:
                raise RSSFetchError(f"{source.key} response is too large: {content_length} bytes")
            body = await self._read_bounded(response)

        parsed = await asyncio.to_thread(feedparser.parse, body)
        if parsed.bozo and not parsed.entries:
            raise RSSFetchError(f"Malformed feed {source.key}: {parsed.bozo_exception}")
        collected_at = datetime.now(UTC)
        items = [
            item
            for entry in parsed.entries
            if (item := self._entry_to_item(source, entry, collected_at)) is not None
        ]
        return FeedFetchResult(
            items=items,
            etag=response_etag,
            last_modified=response_last_modified,
        )

    async def _read_bounded(self, response: aiohttp.ClientResponse) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.content.iter_chunked(64 * 1024):
            size += len(chunk)
            if size > self.max_response_bytes:
                raise RSSFetchError(f"RSS response exceeded {self.max_response_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _entry_to_item(
        source: FeedSource,
        entry: Any,
        collected_at: datetime,
    ) -> CollectedItem | None:
        title = str(entry.get("title", "")).strip()
        url = str(entry.get("link", "")).strip()
        if not title or not url:
            return None
        published_at = _entry_datetime(entry)
        description = str(
            entry.get("summary") or entry.get("description") or entry.get("subtitle") or ""
        )
        author = str(entry.get("author", "")).strip() or None
        external_id = str(entry.get("id") or entry.get("guid") or "").strip()
        if not external_id:
            identity = "\x1f".join(
                [source.key, url, title, published_at.isoformat() if published_at else ""]
            )
            external_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()

        raw = {
            "feed_title": source.name,
            "tags": [str(tag.get("term", "")) for tag in entry.get("tags", []) if tag.get("term")],
        }
        return CollectedItem(
            external_id=external_id,
            source_key=source.key,
            source_name=source.name,
            source_reputation=source.reputation,
            title=title,
            url=url,
            published_at=published_at,
            collected_at=collected_at,
            description=description,
            author=author,
            source_categories=source.default_categories,
            popularity={},
            raw=raw,
        )


def _entry_datetime(entry: Any) -> datetime | None:
    value: struct_time | None = entry.get("published_parsed") or entry.get("updated_parsed")
    if value is None:
        return None
    return datetime.fromtimestamp(calendar.timegm(value), tz=UTC)
