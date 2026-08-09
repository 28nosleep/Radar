"""Release-fix regressions which need PostgreSQL persistence semantics."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import update

from f117.adapters.github import GitHubCollector
from f117.domain import Category, EditorialEnrichment, FeedSource, NormalizedItem, RankedMaterial
from f117.pipeline.classifier import classify_item
from f117.pipeline.normalizer import normalize_item
from f117.services.digest import _select_for_delivery
from f117.storage.database import Database
from f117.storage.models import Base, MaterialModel
from f117.storage.repository import Repository


@pytest_asyncio.fixture
async def repository() -> Repository:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not urlparse(url).path.rstrip("/").endswith("_test"):
        pytest.skip("TEST_DATABASE_URL must name an isolated *_test PostgreSQL database")
    database = Database(url)
    if database.engine.dialect.name != "postgresql":
        pytest.skip("Release integration tests require PostgreSQL")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield Repository(database)
    finally:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await database.dispose()


def _source(key: str = "rss") -> FeedSource:
    return FeedSource(
        key=key,
        name=key.upper(),
        kind="github" if key == "github" else "rss",
        feed_url="https://api.github.com" if key == "github" else f"https://{key}.example/feed",
        reputation=0.9,
        default_categories=[Category.AI],
        github_queries=["topic:ai"] if key == "github" else [],
    )


def _item(
    source_key: str,
    external_id: str,
    *,
    title: str | None = None,
    url: str | None = None,
    description: str = "AI release details",
    metrics: dict[str, float] | None = None,
) -> NormalizedItem:
    now = datetime.now(UTC)
    resolved_title = title or f"AI release {external_id}"
    return NormalizedItem(
        external_id=external_id,
        source_key=source_key,
        source_name=source_key.upper(),
        source_reputation=0.9,
        title=resolved_title,
        url=url or f"https://example.com/{external_id}",
        canonical_url=url or f"https://example.com/{external_id}",
        published_at=now,
        collected_at=now,
        description=description,
        source_categories=[Category.AI],
        categories=[Category.AI],
        popularity=metrics or {},
        content_hash=(resolved_title + description).encode().hex()[:64].ljust(64, "0"),
        normalized_title=resolved_title.casefold(),
    )


async def _add(repository: Repository, item: NormalizedItem) -> UUID:
    state = (await repository.sync_sources([_source(item.source_key)]))[0]
    return (await repository.add_material(state.id, item)).id


@pytest.mark.asyncio
async def test_stale_delivery_lease_recovers_but_ambiguous_requires_manual_recovery(
    repository: Repository,
) -> None:
    material_id = await _add(repository, _item("rss", "lease"))
    assert await repository.begin_delivery(material_id, lease_seconds=300)
    assert not await repository.digest_candidates(
        lookback_hours=24, delivery_claim_lease_seconds=300
    )

    async with repository.database.session() as session:
        await session.execute(
            update(MaterialModel)
            .where(MaterialModel.id == material_id)
            .values(delivery_started_at=datetime.now(UTC) - timedelta(seconds=301))
        )
        await session.commit()
    assert [item.id for item in await repository.digest_candidates(lookback_hours=24)] == [
        material_id
    ]

    await repository.mark_delivery_ambiguous(material_id, error="timeout after request")
    assert not await repository.digest_candidates(lookback_hours=24)
    assert await repository.recover_ambiguous_delivery(material_id)
    assert [item.id for item in await repository.digest_candidates(lookback_hours=24)] == [
        material_id
    ]


@pytest.mark.asyncio
async def test_changed_undelivered_content_invalidates_cached_enrichment(
    repository: Repository,
) -> None:
    state = (await repository.sync_sources([_source()]))[0]
    original = _item("rss", "refresh", description="Old text")
    stored = await repository.add_material(state.id, original)
    await repository.save_enrichment(
        stored.id,
        EditorialEnrichment(
            title_ru="Старый заголовок",
            summary_ru="Старое GPT резюме",
            why_important="Старый смысл",
            post_fit_score=7,
        ),
        model="test",
        usage={"total_tokens": 1},
    )
    await repository.record_editorial_failure(stored.id, error="temporary", retry_delay_seconds=60)
    changed = _item(
        "rss", "refresh", title="AI release v2", description="Completely new release text"
    )
    await repository.refresh_material(state.id, changed)

    refreshed = (await repository.recent_materials(days=1))[0]
    assert refreshed.llm_enrichment is None
    assert refreshed.editorial_attempts == 0
    assert refreshed.editorial_retry_at is None


@pytest.mark.asyncio
async def test_root_aggregate_uses_current_child_raw_metrics_after_correction(
    repository: Repository,
) -> None:
    states = await repository.sync_sources([_source("rss"), _source("github")])
    source_ids = {state.source.key: state.id for state in states}
    root = await repository.add_material(source_ids["rss"], _item("rss", "root"))
    child = await repository.add_material(
        source_ids["github"],
        _item("github", "repo", metrics={"github_stars": 500}),
        duplicate_of_id=root.id,
    )
    await repository.refresh_observation(source_ids["github"], "repo", {"github_stars": 100})

    current = next(item for item in await repository.recent_materials(days=1) if item.id == root.id)
    child_history = await repository.metric_history(child.id)
    assert current.item.popularity["github_stars"] == 100
    assert [snapshot.metrics["github_stars"] for snapshot in child_history] == [500, 100]


@pytest.mark.asyncio
async def test_delivered_github_repo_and_new_release_are_distinct_events(
    repository: Repository,
) -> None:
    source = _source("github")
    state = (await repository.sync_sources([source]))[0]
    repo = {
        "id": 7,
        "full_name": "lab/robot",
        "html_url": "https://github.com/lab/robot",
        "updated_at": "2026-08-09T10:00:00Z",
        "stargazers_count": 100,
    }
    base = classify_item(
        normalize_item(GitHubCollector._repo_to_item(source, repo, datetime.now(UTC), None))
    )
    delivered = await repository.add_material(state.id, base)
    run_id = await repository.create_digest_run(dry_run=False)
    await repository.record_deliveries(run_id, [(delivered.id, "1")])
    release = classify_item(
        normalize_item(
            GitHubCollector._repo_to_item(
                source,
                repo,
                datetime.now(UTC),
                {
                    "id": 99,
                    "tag_name": "v2.0",
                    "html_url": "https://github.com/lab/robot/releases/tag/v2.0",
                },
            )
        )
    )
    release_row = await repository.add_material(state.id, release)

    assert release_row.id != delivered.id
    assert release.external_id == "7:release:99"
    assert release.canonical_url.endswith("/releases/tag/v2.0")


@pytest.mark.asyncio
async def test_due_editorial_retry_gets_bounded_fifo_slot_among_new_candidates(
    repository: Repository,
) -> None:
    state = (await repository.sync_sources([_source()]))[0]
    retry = await repository.add_material(state.id, _item("rss", "retry"))
    await repository.record_editorial_failure(retry.id, error="429", retry_delay_seconds=0)
    for number in range(11):
        await repository.add_material(state.id, _item("rss", f"new-{number}"))
    candidates = await repository.digest_candidates(lookback_hours=24)
    ranked = [
        RankedMaterial(
            material_id=candidate.id,
            title=candidate.item.title,
            url=candidate.item.url,
            source_name=candidate.item.source_name,
            published_at=candidate.item.published_at,
            description=candidate.item.description,
            categories=candidate.item.categories,
            popularity=candidate.item.popularity,
            independent_mentions=candidate.independent_mentions,
            score=10.0 if candidate.id == retry.id else 100.0 - len(candidate.item.external_id),
            score_reasons=[],
        )
        for candidate in candidates
    ]

    selected = _select_for_delivery(ranked, candidates, top_n=3, editorial_retry_slots=1)

    assert selected[0].material_id == retry.id
    assert len(selected) == 3
