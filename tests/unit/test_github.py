from __future__ import annotations

from datetime import UTC, datetime

import pytest

from f117.adapters.github import GitHubCollector
from f117.domain import FeedSource


def test_repository_normalization_includes_releases_and_metrics() -> None:
    source = FeedSource(
        key="github-ai",
        name="GitHub",
        kind="github",
        feed_url="https://api.github.com",
        github_queries=["topic:robotics"],
    )
    item = GitHubCollector._repo_to_item(
        source,
        {
            "id": 17,
            "full_name": "lab/robot",
            "html_url": "https://github.com/lab/robot",
            "description": "A robot stack",
            "updated_at": "2026-08-09T10:00:00Z",
            "stargazers_count": 1200,
            "forks_count": 40,
            "language": "Python",
            "topics": ["robotics", "vision"],
            "owner": {"login": "lab"},
        },
        datetime(2026, 8, 9, tzinfo=UTC),
        {"tag_name": "v1.0"},
    )

    assert item.external_id == "17"
    assert item.author == "lab"
    assert item.popularity["github_stars"] == 1200
    assert item.popularity["forks"] == 40
    assert item.raw["topics"] == ["robotics", "vision"]
    assert "v1.0" in item.description


@pytest.mark.asyncio
async def test_multi_query_search_is_fair_and_one_failure_is_isolated() -> None:
    class Collector(GitHubCollector):
        async def _json(self, _session, _url, *, params=None):  # type: ignore[no-untyped-def]
            assert params is not None
            query = params["q"].split()[0]
            if query == "broken":
                raise RuntimeError("query failed")
            prefix = "a" if query == "first" else "b"
            return {
                "items": [
                    {
                        "id": int(f"{1 if prefix == 'a' else 2}{position}"),
                        "full_name": f"{prefix}/{position}",
                        "html_url": f"https://github.com/{prefix}/{position}",
                        "updated_at": "2026-08-09T10:00:00Z",
                    }
                    for position in range(1, 4)
                ]
            }

    source = FeedSource(
        key="github-ai",
        name="GitHub",
        kind="github",
        feed_url="https://api.github.com",
        github_queries=["first", "second", "broken"],
        github_include_releases=False,
        item_limit=4,
    )
    result = await Collector(timeout_seconds=1, user_agent="test").fetch(source, session=object())

    assert [item.title for item in result.items] == ["a/1", "b/1", "a/2", "b/2"]
