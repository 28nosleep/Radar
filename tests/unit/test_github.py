from __future__ import annotations

from datetime import UTC, datetime

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
