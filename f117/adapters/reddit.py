from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any, Literal

import aiohttp

from f117.adapters.rss import FeedFetchResult, RSSCollector, RSSFetchError
from f117.domain import CollectedItem, FeedSource


class RedditFetchError(RuntimeError):
    pass


class RedditOAuthUnauthorized(RedditFetchError):
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
        self.rss = RSSCollector(
            timeout_seconds=timeout_seconds,
            user_agent=user_agent,
        )

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
            return await self._fetch_rss_fallback(source, session=session)
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
                if response.status == 401:
                    self.access_token = ""
                    raise RedditOAuthUnauthorized("Reddit API returned HTTP 401")
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
        except RedditOAuthUnauthorized:
            return await self._fetch_rss_fallback(source, session=session)
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
                if response.status == 401:
                    raise RedditOAuthUnauthorized("Reddit OAuth returned HTTP 401")
                if response.status != 200:
                    raise RedditFetchError(f"Reddit OAuth returned HTTP {response.status}")
                payload = await response.json(content_type=None)
            token = payload.get("access_token") if isinstance(payload, dict) else None
            if not isinstance(token, str) or not token:
                raise RedditFetchError("Reddit OAuth returned no access token")
            self.access_token = token
            return token

    async def _fetch_rss_fallback(
        self, source: FeedSource, *, session: aiohttp.ClientSession | None
    ) -> FeedFetchResult:
        """Read public listing feeds without fabricating unavailable engagement metrics."""

        assert source.reddit_subreddit is not None
        listings = _rss_listings(source)
        merged: dict[str, CollectedItem] = {}
        etag: str | None = None
        last_modified: str | None = None
        partial = False

        for listing in listings:
            rss_source = source.model_copy(
                update={
                    "feed_url": f"https://www.reddit.com/r/{source.reddit_subreddit}/{listing}.rss"
                }
            )
            try:
                result = await self.rss.fetch(rss_source, session=session)
            except RSSFetchError as exc:
                # ``new`` is the durable baseline.  Optional listing feeds are
                # allowed to fail independently because Reddit does not promise
                # the same availability for every Atom view.
                level = logging.WARNING if listing == "new" else logging.INFO
                self.logger.log(
                    level,
                    "Reddit RSS %s listing skipped for %s: %s",
                    listing,
                    source.key,
                    exc,
                )
                continue

            etag = etag or result.etag
            last_modified = last_modified or result.last_modified
            partial = partial or result.partial
            for item in result.items:
                post_id = _reddit_post_id(item.url)
                if post_id is None:
                    continue
                signals = {"reddit_rss", f"reddit_seen_{listing}"}
                previous = merged.get(post_id)
                if previous is not None:
                    signals.update(previous.qualitative_signals)
                merged[post_id] = item.model_copy(
                    update={
                        "external_id": post_id,
                        "subreddit": source.reddit_subreddit,
                        "media_source": item.media_source and f"reddit:{item.media_source}",
                        "qualitative_signals": sorted(signals),
                    }
                )
        return FeedFetchResult(
            items=list(merged.values()),
            etag=etag,
            last_modified=last_modified,
            partial=partial,
        )

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
            qualitative_signals=["reddit_api"],
            subreddit=str(post.get("subreddit") or source.reddit_subreddit or "") or None,
            media_type=_reddit_media_type(post),
            media_url=_reddit_media_url(post),
            thumbnail_url=_reddit_thumbnail(post),
            media_source="reddit:api" if _reddit_media_url(post) else None,
            raw={"subreddit": post.get("subreddit"), "permalink": permalink},
        )


_POST_ID_RE = re.compile(r"/comments/([a-z0-9]+)(?:/|$)", re.IGNORECASE)


def _reddit_post_id(url: str) -> str | None:
    match = _POST_ID_RE.search(url)
    return match.group(1).lower() if match else None


def _rss_listings(source: FeedSource) -> list[Literal["new", "hot", "rising"]]:
    """Return configured RSS listing views with ``new`` as the resilient baseline."""

    configured: list[Literal["new", "hot", "rising"]] = list(
        dict.fromkeys(source.reddit_rss_listings)
    )
    if "new" not in configured:
        configured.insert(0, "new")
    return configured


def _reddit_thumbnail(post: dict[str, Any]) -> str | None:
    value = post.get("thumbnail")
    return value if isinstance(value, str) and value.startswith(("https://", "http://")) else None


def _reddit_media_url(post: dict[str, Any]) -> str | None:
    preview = post.get("preview")
    if isinstance(preview, dict):
        images = preview.get("images")
        if isinstance(images, list) and images and isinstance(images[0], dict):
            source = images[0].get("source")
            if isinstance(source, dict):
                url = source.get("url")
                if isinstance(url, str) and url.startswith(("https://", "http://")):
                    return url.replace("&amp;", "&")
    return _reddit_thumbnail(post)


def _reddit_media_type(post: dict[str, Any]) -> str:
    if post.get("is_video"):
        return "video"
    return "image" if _reddit_media_url(post) else "none"
