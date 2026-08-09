"""High-value reliability checks against a real PostgreSQL database.

Set TEST_DATABASE_URL to an isolated PostgreSQL database (its name must end in
``_test``) to run these tests. They intentionally exercise concurrent sessions,
not SQLite substitutes.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import update

from f117.adapters.rss import FeedFetchResult
from f117.adapters.telegram import DeliveryCallback, DeliveryReceipt, TelegramError
from f117.config import Settings
from f117.domain import (
    Category,
    CollectedItem,
    EditorialCard,
    EditorialEnrichment,
    FeedSource,
    NormalizedItem,
    RankedMaterial,
)
from f117.services.digest import DigestService
from f117.storage.database import Database
from f117.storage.models import Base, MaterialModel
from f117.storage.repository import Repository


@pytest_asyncio.fixture
async def repository() -> Repository:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    database_name = urlparse(url).path.rstrip("/")
    if not database_name.endswith("_test"):
        pytest.skip("TEST_DATABASE_URL must point at an isolated *_test database")
    database = Database(url)
    if database.engine.dialect.name != "postgresql":
        pytest.skip("Reliability integration tests require PostgreSQL")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield Repository(database)
    finally:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await database.dispose()


def _settings(
    tmp_path: Path,
    *,
    dry_run: bool = False,
    top_n: int = 3,
    delivery_claim_lease_seconds: int = 300,
) -> Settings:
    catalog = tmp_path / "feeds.json"
    catalog.write_text(
        json.dumps(
            [
                {
                    "key": "source-a",
                    "name": "Source A",
                    "feed_url": "https://example.com/feed",
                    "reputation": 0.9,
                    "default_categories": ["ai"],
                }
            ]
        ),
        encoding="utf-8",
    )
    return Settings(
        _env_file=None,
        rss_catalog_path=catalog,
        dry_run=dry_run,
        digest_top_n=top_n,
        candidate_lookback_hours=24,
        editorial_retry_base_seconds=1,
        delivery_claim_lease_seconds=delivery_claim_lease_seconds,
    )


def _source() -> FeedSource:
    return FeedSource(
        key="source-a",
        name="Source A",
        feed_url="https://example.com/feed",
        reputation=0.9,
        default_categories=[Category.AI],
    )


def _item(
    external_id: str,
    *,
    url: str | None = None,
    collected_at: datetime | None = None,
    popularity: dict[str, float] | None = None,
) -> CollectedItem:
    now = collected_at or datetime.now(UTC)
    return CollectedItem(
        external_id=external_id,
        source_key="source-a",
        source_name="Source A",
        source_reputation=0.9,
        title=f"Reliable AI material {external_id}",
        url=url or f"https://example.com/{external_id}",
        published_at=now,
        collected_at=now,
        description="A factual, sufficiently detailed AI update.",
        source_categories=[Category.AI],
        popularity=popularity or {},
    )


def _normalized(item: CollectedItem) -> NormalizedItem:
    return NormalizedItem(
        external_id=item.external_id,
        source_key=item.source_key,
        source_name=item.source_name,
        source_reputation=item.source_reputation,
        title=item.title,
        url=item.url,
        canonical_url=item.url,
        published_at=item.published_at,
        collected_at=item.collected_at,
        description=item.description,
        source_categories=item.source_categories,
        categories=[Category.AI],
        popularity=item.popularity,
        content_hash=(item.external_id * 64)[:64],
        normalized_title=item.title.casefold(),
    )


class _Collector:
    def __init__(self, items: Sequence[CollectedItem] = ()) -> None:
        self.items = list(items)
        self.calls = 0
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None

    async def fetch(self, _: FeedSource, **__: object) -> FeedFetchResult:
        self.calls += 1
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        return FeedFetchResult(self.items, None, None)


class _Enricher:
    def __init__(self, *, crash_on_call: int | None = None) -> None:
        self.calls = 0
        self.crash_on_call = crash_on_call

    async def enrich(self, materials: Sequence[RankedMaterial]) -> list[EditorialCard]:
        self.calls += 1
        if self.crash_on_call == self.calls:
            raise RuntimeError("simulated process crash")
        return [
            EditorialCard(
                material=material,
                enrichment=EditorialEnrichment(
                    title_ru=material.title,
                    summary_ru="Короткое резюме.",
                    why_important="Практически важное обновление.",
                    post_fit_score=8,
                ),
                llm_model="test-model",
                usage={"total_tokens": 1},
            )
            for material in materials
        ]


class _Notifier:
    def __init__(self) -> None:
        self.sent: list[UUID] = []

    async def send(
        self,
        cards: Sequence[EditorialCard],
        *,
        on_delivering: Any = None,
        on_delivered: DeliveryCallback | None = None,
    ) -> list[DeliveryReceipt]:
        receipts = []
        for card in cards:
            if on_delivering is not None:
                await on_delivering(card.material.material_id)
            self.sent.append(card.material.material_id)
            receipt = DeliveryReceipt(card.material.material_id, "100")
            receipts.append(receipt)
            if on_delivered is not None:
                await on_delivered(receipt)
        return receipts


async def _seed(repository: Repository, *items: CollectedItem) -> list[UUID]:
    state = (await repository.sync_sources([_source()]))[0]
    return [(await repository.add_material(state.id, _normalized(item))).id for item in items]


def _service(
    settings: Settings,
    repository: Repository,
    collector: _Collector,
    enricher: _Enricher,
    notifier: _Notifier,
) -> DigestService:
    return DigestService(
        settings=settings,
        repository=repository,
        collector=collector,  # type: ignore[arg-type]
        enricher=enricher,
        notifier=notifier,
    )


@pytest.mark.asyncio
async def test_two_concurrent_runs_have_one_postgres_owner(
    repository: Repository, tmp_path: Path
) -> None:
    collector = _Collector([_item("one")])
    collector.release = asyncio.Event()
    enricher = _Enricher()
    notifier = _Notifier()
    settings = _settings(tmp_path, top_n=1)
    first = _service(settings, repository, collector, enricher, notifier)
    second = _service(settings, repository, collector, enricher, notifier)

    first_task = asyncio.create_task(first.run_once())
    await asyncio.wait_for(collector.started.wait(), timeout=2)
    second_summary = await second.run_once()
    collector.release.set()
    first_summary = await first_task

    assert first_summary.status == "completed"
    assert second_summary.status == "skipped_already_running"
    assert collector.calls == 1
    assert enricher.calls == 1
    assert len(notifier.sent) == 1


@pytest.mark.asyncio
async def test_poison_card_is_quarantined_while_normal_cards_deliver(
    repository: Repository, tmp_path: Path
) -> None:
    poison_id, good_one, good_two = await _seed(
        repository,
        _item("poison", url="javascript:alert(1)"),
        _item("good-one"),
        _item("good-two"),
    )
    notifier = _Notifier()
    summary = await _service(
        _settings(tmp_path), repository, _Collector(), _Enricher(), notifier
    ).run_once()

    assert summary.editorial_failure_count == 1
    assert set(notifier.sent) == {good_one, good_two}
    async with repository.database.session() as session:
        poison = await session.get(MaterialModel, poison_id)
    assert poison is not None and poison.editorial_failed_at is not None


@pytest.mark.asyncio
async def test_old_roots_reenter_on_fresh_metric_or_independent_mention(
    repository: Repository, tmp_path: Path
) -> None:
    old = datetime.now(UTC) - timedelta(days=3)
    metric_root, mention_root = await _seed(
        repository,
        _item("metric-root", collected_at=old, popularity={"points": 10.0}),
        _item("mention-root", collected_at=old),
    )
    second_source = FeedSource(
        key="source-b",
        name="Source B",
        feed_url="https://second.example/feed",
        reputation=0.8,
        default_categories=[Category.AI],
    )
    states = await repository.sync_sources([_source(), second_source])
    await repository.refresh_observation(states[0].id, "metric-root", {"points": 20.0})
    duplicate = _normalized(_item("mention-copy")).model_copy(
        update={"source_key": "source-b", "source_name": "Source B", "source_reputation": 0.8}
    )
    await repository.add_material(states[1].id, duplicate, duplicate_of_id=mention_root)

    candidates = await repository.digest_candidates(
        lookback_hours=_settings(tmp_path).candidate_lookback_hours
    )
    assert {candidate.id for candidate in candidates} == {metric_root, mention_root}


@pytest.mark.asyncio
async def test_successful_openai_card_is_saved_before_later_material_crashes(
    repository: Repository, tmp_path: Path
) -> None:
    first_id, _ = await _seed(
        repository,
        _item("first"),
        _item("second", collected_at=datetime.now(UTC) - timedelta(minutes=1)),
    )
    with pytest.raises(RuntimeError, match="simulated process crash"):
        await _service(
            _settings(tmp_path, top_n=2),
            repository,
            _Collector(),
            _Enricher(crash_on_call=2),
            _Notifier(),
        ).run_once()

    async with repository.database.session() as session:
        first = await session.get(MaterialModel, first_id)
    assert first is not None and first.llm_enrichment is not None


@pytest.mark.asyncio
async def test_telegram_success_then_database_failure_is_held_after_lease_expiry(
    repository: Repository, tmp_path: Path
) -> None:
    material_id = (await _seed(repository, _item("delivery")))[0]

    class _FailingDeliveryRepository(Repository):
        async def record_deliveries(
            self, run_id: UUID, receipts: list[tuple[UUID, str | None]]
        ) -> None:
            del run_id, receipts
            raise RuntimeError("simulated database failure after Telegram success")

    failing_repository = _FailingDeliveryRepository(repository.database)
    notifier = _Notifier()
    with pytest.raises(RuntimeError, match="database failure"):
        await _service(
            _settings(tmp_path, top_n=1),
            failing_repository,
            _Collector(),
            _Enricher(),
            notifier,
        ).run_once()
    assert notifier.sent == [material_id]

    async with repository.database.session() as session:
        row = await session.get(MaterialModel, material_id)
        assert row is not None and row.delivery_ambiguous_at is not None
        await session.execute(
            update(MaterialModel)
            .where(MaterialModel.id == material_id)
            .values(delivery_started_at=datetime.now(UTC) - timedelta(seconds=301))
        )
        await session.commit()
    assert material_id not in {
        candidate.id for candidate in await repository.digest_candidates(lookback_hours=24)
    }


@pytest.mark.asyncio
async def test_configured_delivery_lease_is_used_for_candidate_selection(
    repository: Repository, tmp_path: Path
) -> None:
    material_id = (await _seed(repository, _item("custom-lease")))[0]
    assert await repository.begin_delivery(material_id, lease_seconds=30)
    async with repository.database.session() as session:
        await session.execute(
            update(MaterialModel)
            .where(MaterialModel.id == material_id)
            .values(delivery_started_at=datetime.now(UTC) - timedelta(seconds=31))
        )
        await session.commit()

    notifier = _Notifier()
    await _service(
        _settings(tmp_path, top_n=1, delivery_claim_lease_seconds=30),
        repository,
        _Collector(),
        _Enricher(),
        notifier,
    ).run_once()

    assert notifier.sent == [material_id]


@pytest.mark.asyncio
async def test_ambiguous_telegram_success_is_held_without_automatic_resend(
    repository: Repository, tmp_path: Path
) -> None:
    material_id = (await _seed(repository, _item("ambiguous")))[0]

    class _AmbiguousNotifier(_Notifier):
        async def send(
            self,
            cards: Sequence[EditorialCard],
            *,
            on_delivering: Any = None,
            on_delivered: DeliveryCallback | None = None,
        ) -> list[DeliveryReceipt]:
            del on_delivered
            card = cards[0]
            if on_delivering is not None:
                await on_delivering(card.material.material_id)
            self.sent.append(card.material.material_id)
            raise TelegramError(
                "timeout after Telegram accepted the request",
                ambiguous=True,
                material_id=card.material.material_id,
            )

    with pytest.raises(TelegramError, match="timeout"):
        await _service(
            _settings(tmp_path, top_n=1),
            repository,
            _Collector(),
            _Enricher(),
            _AmbiguousNotifier(),
        ).run_once()
    async with repository.database.session() as session:
        row = await session.get(MaterialModel, material_id)
    assert row is not None and row.delivery_ambiguous_at is not None

    retry_notifier = _Notifier()
    await _service(
        _settings(tmp_path, top_n=1), repository, _Collector(), _Enricher(), retry_notifier
    ).run_once()
    assert retry_notifier.sent == []


@pytest.mark.asyncio
async def test_dry_run_makes_zero_external_calls(repository: Repository, tmp_path: Path) -> None:
    await _seed(repository, _item("dry"))
    collector = _Collector()
    enricher = _Enricher()
    notifier = _Notifier()

    await _service(
        _settings(tmp_path, dry_run=True), repository, collector, enricher, notifier
    ).run_once()

    assert collector.calls == 0
    assert enricher.calls == 0
    assert notifier.sent == []
