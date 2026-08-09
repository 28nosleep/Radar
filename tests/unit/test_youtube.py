from __future__ import annotations

from datetime import UTC, datetime

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
            },
            "statistics": {"viewCount": "5000", "likeCount": "200", "commentCount": "35"},
        },
        datetime(2026, 8, 9, tzinfo=UTC),
    )

    assert item.url == "https://www.youtube.com/watch?v=video-1"
    assert item.author == "Lab"
    assert item.popularity["youtube_views"] == 5000
    assert item.popularity["likes"] == 200
