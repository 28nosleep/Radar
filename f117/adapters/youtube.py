from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import aiohttp

from f117.adapters.rss import FeedFetchResult
from f117.domain import CollectedItem, FeedSource


class YouTubeFetchError(RuntimeError):
    pass


class YouTubeCollector:
    def __init__(self, *, timeout_seconds: float, user_agent: str, api_key: str = "") -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self.api_key = api_key

    async def fetch(
        self,
        source: FeedSource,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> FeedFetchResult:
        del etag, last_modified
        if not self.api_key:
            raise YouTubeFetchError("F117_YOUTUBE_API_KEY is required for enabled YouTube sources")
        if not source.youtube_channel_ids and not source.youtube_queries:
            raise YouTubeFetchError(
                f"{source.key} must declare youtube_channel_ids or youtube_queries"
            )
        owns_session = session is None
        if session is None:
            session = aiohttp.ClientSession(timeout=self.timeout, headers=self.headers)
        try:
            video_ids: list[str] = []
            seen_ids: set[str] = set()
            for channel_id in source.youtube_channel_ids:
                for video_id in await self._search(session, {"channelId": channel_id}):
                    if video_id not in seen_ids:
                        seen_ids.add(video_id)
                        video_ids.append(video_id)
            for query in source.youtube_queries:
                for video_id in await self._search(session, {"q": query}):
                    if video_id not in seen_ids:
                        seen_ids.add(video_id)
                        video_ids.append(video_id)
            videos = (
                await self._json(
                    session,
                    "videos",
                    {
                        "id": ",".join(video_ids[: source.item_limit]),
                        "part": "snippet,statistics",
                    },
                )
                if video_ids
                else {"items": []}
            )
            raw_items = videos.get("items", []) if isinstance(videos, dict) else []
            by_id = {
                str(video.get("id")): video
                for video in raw_items
                if isinstance(video, dict) and isinstance(video.get("id"), str)
            }
            items = [by_id[video_id] for video_id in video_ids if video_id in by_id]
            collected_at = datetime.now(UTC)
            return FeedFetchResult(
                items=[
                    self._video_to_item(source, video, collected_at)
                    for video in items
                    if isinstance(video, dict)
                ],
                etag=None,
                last_modified=None,
            )
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise YouTubeFetchError(f"Failed to fetch {source.key}: {exc}") from exc
        finally:
            if owns_session:
                await session.close()

    async def _search(self, session: aiohttp.ClientSession, extra: dict[str, str]) -> list[str]:
        payload = await self._json(
            session,
            "search",
            {"part": "snippet", "type": "video", "order": "date", "maxResults": "50", **extra},
        )
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [
            str(item.get("id", {}).get("videoId"))
            for item in items
            if isinstance(item, dict) and item.get("id", {}).get("videoId")
        ]

    async def _json(
        self, session: aiohttp.ClientSession, endpoint: str, params: dict[str, str]
    ) -> object:
        async with session.get(
            f"https://www.googleapis.com/youtube/v3/{endpoint}",
            params={**params, "key": self.api_key},
            headers=self.headers,
        ) as response:
            if response.status != 200:
                raise YouTubeFetchError(f"YouTube returned HTTP {response.status}")
            return await response.json(content_type=None)

    @staticmethod
    def _video_to_item(
        source: FeedSource, video: dict[str, Any], collected_at: datetime
    ) -> CollectedItem:
        snippet_value = video.get("snippet")
        statistics_value = video.get("statistics")
        snippet: dict[str, Any] = dict(snippet_value) if isinstance(snippet_value, dict) else {}
        statistics: dict[str, Any] = (
            dict(statistics_value) if isinstance(statistics_value, dict) else {}
        )
        video_id = str(video.get("id"))
        return CollectedItem(
            external_id=video_id,
            source_key=source.key,
            source_name=source.name,
            source_reputation=source.reputation,
            title=str(snippet.get("title") or video_id),
            url=f"https://www.youtube.com/watch?v={video_id}",
            published_at=_parse_youtube_date(snippet.get("publishedAt")),
            collected_at=collected_at,
            description=str(snippet.get("description") or ""),
            author=str(snippet.get("channelTitle") or "").strip() or None,
            source_categories=source.default_categories,
            popularity={
                "youtube_views": float(statistics.get("viewCount") or 0),
                "likes": float(statistics.get("likeCount") or 0),
                "comments": float(statistics.get("commentCount") or 0),
            },
            raw={"channel_id": snippet.get("channelId")},
        )


def _parse_youtube_date(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
