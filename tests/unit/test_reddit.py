from __future__ import annotations

from datetime import UTC, datetime

from f117.adapters.reddit import RedditCollector, RedditOAuthUnauthorized
from f117.adapters.rss import FeedFetchResult
from f117.domain import Category, CollectedItem, FeedSource


def test_reddit_post_normalization_keeps_discussion_metrics_and_text() -> None:
    source = FeedSource(
        key="reddit-control",
        name="r/ControlProblem",
        kind="reddit",
        feed_url="https://www.reddit.com",
        reddit_subreddit="ControlProblem",
        reputation=0.35,
        default_categories=[Category.AI],
    )
    item = RedditCollector._post_to_item(
        source,
        {
            "id": "abc",
            "title": "New agent eval",
            "selftext": "Technical details",
            "author": "alice",
            "created_utc": 1_700_000_000,
            "score": 80,
            "num_comments": 21,
            "subreddit": "ControlProblem",
            "permalink": "/r/ControlProblem/comments/abc/new_agent_eval/",
        },
        datetime(2026, 8, 9, tzinfo=UTC),
    )

    assert item is not None
    assert item.popularity == {"reddit_upvotes": 80.0, "reddit_comments": 21.0}
    assert item.description == "Technical details"
    assert item.url.startswith("https://www.reddit.com/r/ControlProblem")


async def test_reddit_without_credentials_uses_rss_fallback() -> None:
    source = FeedSource(
        key="reddit",
        name="Reddit",
        kind="reddit",
        feed_url="https://www.reddit.com",
        reddit_subreddit="robotics",
    )

    collector = RedditCollector(timeout_seconds=1, user_agent="test")

    class RSS:
        async def fetch(self, rss_source, **kwargs):  # type: ignore[no-untyped-def]
            assert str(rss_source.feed_url).endswith("/r/robotics/new.rss")
            return FeedFetchResult(
                items=[
                    CollectedItem(
                        external_id="atom-id",
                        source_key=source.key,
                        source_name=source.name,
                        source_reputation=source.reputation,
                        title="Robot image",
                        url="https://www.reddit.com/r/robotics/comments/AbC123/robot_image/",
                        collected_at=datetime.now(UTC),
                        media_type="image",
                        media_url="https://i.redd.it/robot.jpg",
                    )
                ],
                etag=None,
                last_modified=None,
            )

    collector.rss = RSS()  # type: ignore[assignment]
    result = await collector.fetch(source)

    assert result.items[0].external_id == "abc123"
    assert result.items[0].subreddit == "robotics"
    assert result.items[0].popularity == {}


async def test_reddit_api_401_uses_rss_fallback() -> None:
    source = FeedSource(
        key="reddit",
        name="Reddit",
        kind="reddit",
        feed_url="https://www.reddit.com",
        reddit_subreddit="ai",
    )
    collector = RedditCollector(
        timeout_seconds=1, user_agent="test", client_id="id", client_secret="secret"
    )

    async def unauthorized(_session: object) -> str:
        raise RedditOAuthUnauthorized("401")

    async def fallback(_source: FeedSource, *, session: object | None) -> FeedFetchResult:
        return FeedFetchResult(
            items=[
                CollectedItem(
                    external_id="rss-post",
                    source_key="reddit",
                    source_name="Reddit",
                    source_reputation=0.5,
                    title="Fallback",
                    url="https://www.reddit.com/r/ai/comments/rss-post/fallback/",
                    collected_at=datetime.now(UTC),
                )
            ],
            etag=None,
            last_modified=None,
        )

    collector._access_token = unauthorized  # type: ignore[method-assign]
    collector._fetch_rss_fallback = fallback  # type: ignore[method-assign]
    result = await collector.fetch(source, session=object())

    assert [item.external_id for item in result.items] == ["rss-post"]


async def test_reddit_working_oauth_does_not_fetch_rss_duplicate() -> None:
    source = FeedSource(
        key="reddit",
        name="Reddit",
        kind="reddit",
        feed_url="https://www.reddit.com",
        reddit_subreddit="ai",
    )
    collector = RedditCollector(
        timeout_seconds=1, user_agent="test", client_id="id", client_secret="secret"
    )

    async def token(_session: object) -> str:
        return "token"

    async def no_rss(_source: FeedSource, *, session: object | None) -> FeedFetchResult:
        raise AssertionError("RSS must not run while OAuth works")

    class Response:
        status = 200

        async def __aenter__(self) -> Response:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def json(self, *, content_type: object = None) -> dict[str, object]:
            del content_type
            return {
                "data": {
                    "children": [
                        {
                            "data": {
                                "id": "api-post",
                                "title": "OAuth item",
                                "created_utc": 1_700_000_000,
                                "subreddit": "ai",
                            }
                        }
                    ]
                }
            }

    class Session:
        def get(self, _: str, **kwargs: object) -> Response:
            assert kwargs["headers"] == {
                "User-Agent": "test",
                "Accept": "application/json",
                "Authorization": "Bearer token",
            }
            return Response()

    collector._access_token = token  # type: ignore[method-assign]
    collector._fetch_rss_fallback = no_rss  # type: ignore[method-assign]
    result = await collector.fetch(source, session=Session())

    assert [item.external_id for item in result.items] == ["api-post"]
