from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import pytest
import pytest_asyncio

from f117.domain import Category, FeedSource, NormalizedItem
from f117.storage.database import Database
from f117.storage.models import Base
from f117.storage.repository import Repository


@pytest_asyncio.fixture
async def repository() -> Repository:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not urlparse(url).path.rstrip("/").endswith("_test"):
        pytest.skip("TEST_DATABASE_URL must name an isolated *_test PostgreSQL database")
    database = Database(url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield Repository(database)
    finally:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await database.dispose()


def _source(key: str, name: str) -> FeedSource:
    return FeedSource(
        key=key,
        name=name,
        feed_url=f"https://{key}.example/feed",
        reputation=0.8,
        default_categories=[Category.AI],
    )


def _item(
    source_key: str,
    external_id: str,
    *,
    title: str = "AI project release",
    description: str = "Initial description",
    url: str = "https://example.com/project",
    metrics: dict[str, float] | None = None,
) -> NormalizedItem:
    now = datetime.now(UTC)
    return NormalizedItem(
        external_id=external_id,
        source_key=source_key,
        source_name=source_key,
        source_reputation=0.8,
        title=title,
        url=url,
        canonical_url=url,
        published_at=now,
        collected_at=now,
        description=description,
        source_categories=[Category.AI],
        categories=[Category.AI],
        popularity=metrics or {},
        content_hash=(title + description).encode().hex()[:64].ljust(64, "0"),
        normalized_title=title.casefold(),
    )


@pytest.mark.asyncio
async def test_refresh_updates_changed_content_and_reclassifies(repository: Repository) -> None:
    source = (await repository.sync_sources([_source("rss", "RSS")]))[0]
    original = _item("rss", "same", title="Project", description="Old summary")
    stored = await repository.add_material(source.id, original)
    changed = _item(
        "rss",
        "same",
        title="Project releases AI robot version 2.0",
        description="New robotics release summary",
        url="https://example.com/project/v2",
    ).model_copy(update={"categories": [Category.AI, Category.ROBOTICS]})

    await repository.refresh_material(source.id, changed)
    refreshed = (await repository.recent_materials(days=1))[0]

    assert refreshed.id == stored.id
    assert refreshed.item.title == changed.title
    assert refreshed.item.description == changed.description
    assert refreshed.item.canonical_url == changed.canonical_url
    assert Category.ROBOTICS in refreshed.item.categories


@pytest.mark.asyncio
async def test_exact_canonical_lookup_finds_material_outside_fuzzy_window(
    repository: Repository,
) -> None:
    source = (await repository.sync_sources([_source("rss", "RSS")]))[0]
    old = datetime.now(UTC) - timedelta(days=30)
    item = _item("rss", "old-url").model_copy(update={"published_at": old, "collected_at": old})
    stored = await repository.add_material(source.id, item)

    exact = await repository.material_by_canonical_url(item.canonical_url)

    assert exact is not None and exact.id == stored.id


@pytest.mark.asyncio
async def test_duplicate_metrics_aggregate_on_root_and_reddit_is_one_family(
    repository: Repository,
) -> None:
    sources = await repository.sync_sources(
        [
            _source("rss", "RSS"),
            _source("github", "GitHub"),
            _source("reddit-ai", "Reddit AI"),
            _source("reddit-llm", "Reddit LLM"),
        ]
    )
    source_ids = {state.source.key: state.id for state in sources}
    root = await repository.add_material(source_ids["rss"], _item("rss", "root"))
    await repository.add_material(
        source_ids["github"],
        _item("github", "repo", metrics={"github_stars": 2_000}),
        duplicate_of_id=root.id,
    )
    await repository.add_material(
        source_ids["reddit-ai"],
        _item("reddit-ai", "post-a", metrics={"reddit_upvotes": 400}),
        duplicate_of_id=root.id,
    )
    await repository.add_material(
        source_ids["reddit-llm"],
        _item("reddit-llm", "post-b", metrics={"reddit_upvotes": 700}),
        duplicate_of_id=root.id,
    )

    root_row = next(
        item for item in await repository.recent_materials(days=1) if item.id == root.id
    )
    history = await repository.metric_history(root.id)

    assert root_row.item.popularity["github_stars"] == 2_000
    assert root_row.item.popularity["reddit_upvotes"] == 700
    assert root_row.independent_mentions == 3
    assert history[-1].metrics["github_stars"] == 2_000
