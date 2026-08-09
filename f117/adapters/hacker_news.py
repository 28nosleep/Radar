from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import aiohttp

from f117.adapters.rss import FeedFetchResult
from f117.domain import CollectedItem, FeedSource


class HackerNewsFetchError(RuntimeError):
    pass


class HackerNewsCollector:
    """Fetch a configurable Hacker News listing through the official Firebase API."""

    def __init__(self, *, timeout_seconds: float = 20.0, user_agent: str) -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.headers = {"User-Agent": user_agent, "Accept": "application/json"}

    async def fetch(
        self,
        source: FeedSource,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> FeedFetchResult:
        del etag, last_modified
        owns_session = session is None
        if session is None:
            session = aiohttp.ClientSession(timeout=self.timeout, headers=self.headers)
        try:
            base = str(source.feed_url).rstrip("/")
            listing = await self._json(session, f"{base}/{source.collection}stories.json")
            if not isinstance(listing, list):
                raise HackerNewsFetchError(f"{source.key} returned an invalid story list")
            story_ids = [
                story_id for story_id in listing[: source.item_limit] if isinstance(story_id, int)
            ]
            stories = await asyncio.gather(
                *(self._json(session, f"{base}/item/{story_id}.json") for story_id in story_ids),
                return_exceptions=True,
            )
            collected_at = datetime.now(UTC)
            items = [
                item
                for story in stories
                if isinstance(story, dict)
                and (item := self._story_to_item(source, story, collected_at)) is not None
            ]
            return FeedFetchResult(items=items, etag=None, last_modified=None)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise HackerNewsFetchError(f"Failed to fetch {source.key}: {exc}") from exc
        finally:
            if owns_session:
                await session.close()

    async def _json(self, session: aiohttp.ClientSession, url: str) -> object:
        async with session.get(url, headers=self.headers) as response:
            if response.status != 200:
                raise HackerNewsFetchError(f"Hacker News returned HTTP {response.status}")
            return await response.json(content_type=None)

    @staticmethod
    def _story_to_item(
        source: FeedSource, story: dict[str, Any], collected_at: datetime
    ) -> CollectedItem | None:
        if story.get("type") != "story" or story.get("dead") or story.get("deleted"):
            return None
        story_id = story.get("id")
        title = str(story.get("title") or "").strip()
        if not isinstance(story_id, int) or not title:
            return None
        url = str(story.get("url") or f"https://news.ycombinator.com/item?id={story_id}")
        timestamp = story.get("time")
        published_at = (
            datetime.fromtimestamp(timestamp, tz=UTC) if isinstance(timestamp, int) else None
        )
        return CollectedItem(
            external_id=str(story_id),
            source_key=source.key,
            source_name=source.name,
            source_reputation=source.reputation,
            title=title,
            url=url,
            published_at=published_at,
            collected_at=collected_at,
            description=str(story.get("text") or ""),
            author=str(story.get("by") or "").strip() or None,
            source_categories=source.default_categories,
            popularity={
                "hn_points": float(story.get("score") or 0),
                "hn_comments": float(story.get("descendants") or 0),
            },
            raw={"hn_id": story_id, "hn_listing": source.collection},
        )
