from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import aiohttp

from f117.adapters.rss import FeedFetchResult
from f117.domain import CollectedItem, FeedSource


class RedditFetchError(RuntimeError):
    pass


class RedditCollector:
    def __init__(
        self,
        *,
        timeout_seconds: float,
        user_agent: str,
        client_id: str = "",
        client_secret: str = "",
    ) -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = ""
        self.token_lock = asyncio.Lock()
        self.logger = logging.getLogger(__name__)

    async def fetch(
        self,
        source: FeedSource,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> FeedFetchResult:
        del etag, last_modified
        if not source.reddit_subreddit:
            raise RedditFetchError(f"{source.key} must declare reddit_subreddit")
        if not self.client_id or not self.client_secret:
            self.logger.info(
                "Reddit source %s skipped: OAuth credentials are not configured", source.key
            )
            return FeedFetchResult(items=[], etag=None, last_modified=None)
        owns_session = session is None
        if session is None:
            session = aiohttp.ClientSession(timeout=self.timeout, headers=self.headers)
        try:
            token = await self._access_token(session)
            url = (
                f"https://oauth.reddit.com/r/{source.reddit_subreddit}/{source.reddit_listing}.json"
            )
            async with session.get(
                url,
                params={"limit": str(source.item_limit), "raw_json": "1"},
                headers={**self.headers, "Authorization": f"Bearer {token}"},
            ) as response:
                if response.status != 200:
                    raise RedditFetchError(f"Reddit returned HTTP {response.status}")
                payload = await response.json(content_type=None)
            children = (
                payload.get("data", {}).get("children", []) if isinstance(payload, dict) else []
            )
            collected_at = datetime.now(UTC)
            return FeedFetchResult(
                items=[
                    item
                    for child in children
                    if isinstance(child, dict)
                    and isinstance((data := child.get("data")), dict)
                    and (item := self._post_to_item(source, data, collected_at)) is not None
                ],
                etag=None,
                last_modified=None,
            )
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise RedditFetchError(f"Failed to fetch {source.key}: {exc}") from exc
        finally:
            if owns_session:
                await session.close()

    async def _access_token(self, session: aiohttp.ClientSession) -> str:
        async with self.token_lock:
            if self.access_token:
                return self.access_token
            async with session.post(
                "https://www.reddit.com/api/v1/access_token",
                data={"grant_type": "client_credentials"},
                auth=aiohttp.BasicAuth(self.client_id, self.client_secret),
                headers=self.headers,
            ) as response:
                if response.status != 200:
                    raise RedditFetchError(f"Reddit OAuth returned HTTP {response.status}")
                payload = await response.json(content_type=None)
            token = payload.get("access_token") if isinstance(payload, dict) else None
            if not isinstance(token, str) or not token:
                raise RedditFetchError("Reddit OAuth returned no access token")
            self.access_token = token
            return token

    @staticmethod
    def _post_to_item(
        source: FeedSource, post: dict[str, Any], collected_at: datetime
    ) -> CollectedItem | None:
        post_id = post.get("id")
        title = str(post.get("title") or "").strip()
        if not isinstance(post_id, str) or not title or post.get("removed_by_category"):
            return None
        permalink = str(post.get("permalink") or "")
        external_url = str(post.get("url_overridden_by_dest") or "")
        url = external_url or f"https://www.reddit.com{permalink}"
        timestamp = post.get("created_utc")
        return CollectedItem(
            external_id=post_id,
            source_key=source.key,
            source_name=source.name,
            source_reputation=source.reputation,
            title=title,
            url=url,
            published_at=datetime.fromtimestamp(timestamp, tz=UTC)
            if isinstance(timestamp, (int, float))
            else None,
            collected_at=collected_at,
            description=str(post.get("selftext") or ""),
            author=str(post.get("author") or "").strip() or None,
            source_categories=source.default_categories,
            popularity={
                "reddit_upvotes": float(post.get("score") or 0),
                "reddit_comments": float(post.get("num_comments") or 0),
            },
            raw={"subreddit": post.get("subreddit"), "permalink": permalink},
        )
