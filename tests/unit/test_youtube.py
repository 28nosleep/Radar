from __future__ import annotations

from datetime import UTC, datetime

import pytest

from f117.adapters.youtube import YouTubeCollector
from f117.domain import FeedSource


def test_video_normalization_preserves_available_statistics() -> None:
    source = FeedSource(
        key="youtube-ai",
        name="YouTube",
        kind="youtube",
        feed_url="https://www.googleapis.com/youtube/v3",
        youtube_queries=["robotics"],
    )
    item = YouTubeCollector._video_to_item(
        source,
        {
            "id": "video-1",
            "snippet": {
                "title": "Robot demo",
                "description": "A demo",
                "channelTitle": "Lab",
                "channelId": "channel",
                "publishedAt": "2026-08-09T10:00:00Z",
                "thumbnails": {"high": {"url": "https://img.youtube.com/video-1/hq.jpg"}},
            },
            "statistics": {"viewCount": "5000", "likeCount": "200", "commentCount": "35"},
        },
        datetime(2026, 8, 9, tzinfo=UTC),
    )

    assert item.url == "https://www.youtube.com/watch?v=video-1"
    assert item.author == "Lab"
    assert item.popularity["youtube_views"] == 5000
    assert item.popularity["likes"] == 200
    assert item.media_type == "video"
    assert item.thumbnail_url == "https://img.youtube.com/video-1/hq.jpg"


@pytest.mark.asyncio
async def test_video_order_follows_date_search_order_not_lexical_ids() -> None:
    class Collector(YouTubeCollector):
        async def _search(self, _session, extra):  # type: ignore[no-untyped-def]
            return ["z-new", "a-old"] if "q" in extra else []

        async def _json(self, _session, endpoint, params):  # type: ignore[no-untyped-def]
            assert endpoint == "videos"
            assert params["id"] == "z-new,a-old"
            return {
                "items": [
                    {"id": "a-old", "snippet": {"title": "old"}, "statistics": {}},
                    {"id": "z-new", "snippet": {"title": "new"}, "statistics": {}},
                ]
            }

    source = FeedSource(
        key="youtube-ai",
        name="YouTube",
        kind="youtube",
        feed_url="https://www.googleapis.com/youtube/v3",
        youtube_queries=["ai"],
        item_limit=2,
    )
    result = await Collector(timeout_seconds=1, user_agent="test", api_key="key").fetch(
        source, session=object()
    )

    assert [item.external_id for item in result.items] == ["z-new", "a-old"]
