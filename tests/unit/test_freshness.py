from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from f117.domain import Category, NormalizedItem, StoredMaterial
from f117.services.digest import DigestService


def _service() -> DigestService:
    service = DigestService.__new__(DigestService)
    service.settings = SimpleNamespace(
        freshness_daily_max_age_days=30,
        freshness_youtube_daily_max_age_days=30,
        freshness_funny_wtf_max_age_days=14,
        freshness_discovery_max_age_days=14,
    )
    return service


def _material(source_key: str, age_days: int) -> StoredMaterial:
    published_at = datetime.now(UTC) - timedelta(days=age_days)
    return StoredMaterial(
        id=uuid4(),
        item=NormalizedItem(
            external_id="video",
            source_key=source_key,
            source_name="YouTube",
            source_reputation=0.7,
            title="Video",
            url="https://www.youtube.com/watch?v=video",
            canonical_url="https://www.youtube.com/watch?v=video",
            published_at=published_at,
            collected_at=datetime.now(UTC),
            description="",
            categories=[Category.AI],
            popularity={"youtube_views": 1_000_000},
            content_hash="a" * 64,
            normalized_title="video",
        ),
    )


def test_old_youtube_video_is_not_a_daily_candidate_but_fresh_one_is() -> None:
    service = _service()

    assert service._passes_base_freshness(_material("youtube-ai", 600)) is False
    assert service._passes_base_freshness(_material("youtube-ai", 7)) is True


def test_github_keeps_release_or_growth_based_freshness_without_repo_age_gate() -> None:
    assert _service()._passes_base_freshness(_material("github-trending", 600)) is True
