from __future__ import annotations

from datetime import UTC, datetime

from f117.adapters.reddit import RedditCollector
from f117.domain import Category, FeedSource


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


async def test_reddit_without_credentials_is_skipped_without_network() -> None:
    source = FeedSource(
        key="reddit",
        name="Reddit",
        kind="reddit",
        feed_url="https://www.reddit.com",
        reddit_subreddit="robotics",
    )

    result = await RedditCollector(timeout_seconds=1, user_agent="test").fetch(source)

    assert result.items == []
