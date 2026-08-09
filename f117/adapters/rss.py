from __future__ import annotations

import asyncio
import calendar
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from time import struct_time
from typing import Any
from urllib.parse import parse_qs, urlsplit

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
    partial: bool = False


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
        parsed_items = [
            item
            for entry in parsed.entries
            if (item := self._entry_to_item(source, entry, collected_at)) is not None
        ]
        return FeedFetchResult(
            items=parsed_items[: source.item_limit],
            etag=response_etag,
            last_modified=response_last_modified,
            partial=len(parsed_items) > source.item_limit,
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
        media_type, media_url, thumbnail_url, media_source = _entry_media(entry, description)
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
            media_type=media_type,
            media_url=media_url,
            thumbnail_url=thumbnail_url,
            media_source=media_source,
            raw=raw,
        )


def _entry_datetime(entry: Any) -> datetime | None:
    value: struct_time | None = entry.get("published_parsed") or entry.get("updated_parsed")
    if value is None:
        return None
    return datetime.fromtimestamp(calendar.timegm(value), tz=UTC)


_IMAGE_RE = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)", re.IGNORECASE)
_REDDIT_NON_CONTENT_IMAGE_RE = re.compile(
    r"(?:avatar|icon|logo|pixel|placeholder|redditstatic)", re.IGNORECASE
)


def _entry_media(entry: Any, description: str) -> tuple[str, str | None, str | None, str | None]:
    """Use publisher-supplied feed metadata only; no page fetches at collection time."""

    def url_from(value: object) -> str | None:
        if isinstance(value, dict):
            value = value.get("url") or value.get("href")
        if isinstance(value, str) and value.strip().startswith(("https://", "http://")):
            return unescape(value.strip())
        return None

    def is_usable_image(url: str) -> bool:
        parsed = urlsplit(url)
        if _REDDIT_NON_CONTENT_IMAGE_RE.search(parsed.path) or _REDDIT_NON_CONTENT_IMAGE_RE.search(
            parsed.hostname or ""
        ):
            return False
        dimensions = parse_qs(parsed.query)
        try:
            width = int(dimensions.get("width", ["0"])[0])
            height = int(dimensions.get("height", ["0"])[0])
        except ValueError:
            return False
        return width <= 0 or height <= 0 or max(width, height) >= 120

    def html_images() -> list[str]:
        fragments = [description]
        content = entry.get("content") or []
        for value in content if isinstance(content, list) else [content]:
            if isinstance(value, dict) and isinstance(value.get("value"), str):
                fragments.append(value["value"])
            elif isinstance(value, str):
                fragments.append(value)
        return [
            url
            for fragment in fragments
            for matched in _IMAGE_RE.findall(fragment)
            if (url := url_from(matched)) is not None and is_usable_image(url)
        ]

    media_content = entry.get("media_content") or []
    for value in media_content if isinstance(media_content, list) else [media_content]:
        if url := url_from(value):
            medium = value.get("medium") if isinstance(value, dict) else None
            if medium == "video":
                return "video", url, None, "rss:media"
            if is_usable_image(url):
                return "image", url, None, "rss:media"
    for url in html_images():
        return "image", url, url, "rss:content"
    media_thumbnail = entry.get("media_thumbnail") or []
    for value in media_thumbnail if isinstance(media_thumbnail, list) else [media_thumbnail]:
        if (url := url_from(value)) and is_usable_image(url):
            return "image", url, url, "rss:thumbnail"
    enclosures = entry.get("enclosures") or []
    for value in enclosures if isinstance(enclosures, list) else [enclosures]:
        if url := url_from(value):
            mime = value.get("type", "") if isinstance(value, dict) else ""
            if str(mime).startswith("image/") and is_usable_image(url):
                return "image", url, None, "rss:enclosure"
    image = entry.get("image")
    if (url := url_from(image)) and is_usable_image(url):
        return "image", url, url, "rss:image"
    return "none", None, None, None
