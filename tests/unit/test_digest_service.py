from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from f117.adapters.rss import FeedFetchResult
from f117.adapters.telegram import DeliveryCallback, DeliveryReceipt
from f117.config import Settings
from f117.domain import (
    Category,
    CollectedItem,
    EditorialCard,
    EditorialEnrichment,
    FeedSource,
    NormalizedItem,
    RankedMaterial,
    StoredMaterial,
)
from f117.services.digest import DigestService, _select_for_delivery
from f117.storage.repository import SourceState


class _MemoryRepository:
    def __init__(self) -> None:
        self.source_ids: dict[str, UUID] = {}
        self.materials: list[StoredMaterial] = []
        self.runs: dict[UUID, dict[str, object]] = {}
        self.recorded_deliveries: list[tuple[UUID, str | None]] = []
        self.saved_enrichments = 0
        self.source_results: list[dict[str, object]] = []
        self.selected_ids: list[UUID] = []

    async def create_digest_run(self, *, dry_run: bool) -> UUID:
        run_id = uuid4()
        self.runs[run_id] = {"dry_run": dry_run, "status": "running"}
        return run_id

    async def sync_sources(self, sources: list[FeedSource]) -> list[SourceState]:
        states = []
        for source in sources:
            source_id = self.source_ids.setdefault(source.key, uuid4())
            states.append(SourceState(source_id, source, None, None))
        return states

    async def record_source_result(self, *_: object, **values: object) -> None:
        self.source_results.append(values)

    async def recent_materials(self, *, days: int) -> list[StoredMaterial]:
        del days
        return list(self.materials)

    async def has_material(self, source_id: UUID, external_id: str) -> bool:
        source_key = next(key for key, value in self.source_ids.items() if value == source_id)
        return any(
            material.item.source_key == source_key and material.item.external_id == external_id
            for material in self.materials
        )

    async def refresh_observation(
        self, source_id: UUID, external_id: str, metrics: dict[str, float]
    ) -> None:
        del source_id, external_id, metrics

    async def add_material(
        self,
        source_id: UUID,
        item: NormalizedItem,
        *,
        duplicate_of_id: UUID | None = None,
    ) -> StoredMaterial:
        assert self.source_ids[item.source_key] == source_id
        if duplicate_of_id is not None:
            already_represented = any(
                material.item.source_key == item.source_key
                and (material.id == duplicate_of_id or material.duplicate_of_id == duplicate_of_id)
                for material in self.materials
            )
            if not already_represented:
                self.materials = [
                    (
                        material.model_copy(
                            update={"independent_mentions": material.independent_mentions + 1}
                        )
                        if material.id == duplicate_of_id
                        else material
                    )
                    for material in self.materials
                ]
        stored = StoredMaterial(
            id=uuid4(),
            item=item,
            duplicate_of_id=duplicate_of_id,
        )
        self.materials.append(stored)
        return stored

    async def digest_candidates(self, *, lookback_hours: int) -> list[StoredMaterial]:
        del lookback_hours
        return [
            material
            for material in self.materials
            if material.duplicate_of_id is None and material.delivered_at is None
        ]

    async def save_rankings(self, ranked: list[RankedMaterial]) -> None:
        values = {material.material_id: material for material in ranked}
        self.materials = [
            material.model_copy(
                update={
                    "score": values[material.id].score,
                    "score_reasons": values[material.id].score_reasons,
                }
            )
            if material.id in values
            else material
            for material in self.materials
        ]

    async def metric_histories(self, material_ids: list[UUID]) -> dict[UUID, list[object]]:
        return {material_id: [] for material_id in material_ids}

    async def save_discovery_scores(self, values: dict[UUID, float]) -> None:
        self.materials = [
            material.model_copy(update={"discovery_score": values[material.id]})
            if material.id in values
            else material
            for material in self.materials
        ]

    async def record_selection(self, material_ids: list[UUID]) -> None:
        self.selected_ids.extend(material_ids)

    async def save_enrichment(
        self,
        material_id: UUID,
        enrichment: EditorialEnrichment,
        *,
        model: str,
        usage: dict[str, int],
    ) -> None:
        self.saved_enrichments += 1
        self.materials = [
            material.model_copy(
                update={
                    "llm_enrichment": enrichment,
                    "llm_model": model,
                    "llm_usage": usage,
                }
            )
            if material.id == material_id
            else material
            for material in self.materials
        ]

    async def record_deliveries(
        self,
        run_id: UUID,
        receipts: list[tuple[UUID, str | None]],
    ) -> None:
        del run_id
        self.recorded_deliveries.extend(receipts)

    async def finish_digest_run(self, run_id: UUID, **values: object) -> None:
        self.runs[run_id].update(values)


class _Collector:
    def __init__(self, items: dict[str, list[CollectedItem]]) -> None:
        self.items = items

    async def fetch(
        self,
        source: FeedSource,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FeedFetchResult:
        del etag, last_modified
        return FeedFetchResult(self.items[source.key], "etag", "modified")


class _CountingEnricher:
    def __init__(self) -> None:
        self.calls: list[list[UUID]] = []

    async def enrich(self, materials: Sequence[RankedMaterial]) -> list[EditorialCard]:
        self.calls.append([material.material_id for material in materials])
        return [
            EditorialCard(
                material=material,
                enrichment=EditorialEnrichment(
                    title_ru=f"RU: {material.title}",
                    summary_ru="summary",
                    why_important="important",
                    post_fit_score=8,
                ),
                llm_model="test-model",
                usage={"total_tokens": 100},
            )
            for material in materials
        ]


class _Notifier:
    def __init__(self) -> None:
        self.calls: list[list[EditorialCard]] = []

    async def send(
        self,
        cards: Sequence[EditorialCard],
        *,
        on_delivered: DeliveryCallback | None = None,
    ) -> list[DeliveryReceipt]:
        self.calls.append(list(cards))
        receipts = [DeliveryReceipt(card.material.material_id, "42") for card in cards]
        if on_delivered is not None:
            for receipt in receipts:
                await on_delivered(receipt)
        return receipts


def _settings(tmp_path: Path, *, dry_run: bool = True) -> Settings:
    catalog = tmp_path / "feeds.json"
    catalog.write_text(
        json.dumps(
            [
                {
                    "key": "source-a",
                    "name": "Source A",
                    "feed_url": "https://a.example/feed",
                    "reputation": 0.9,
                    "default_categories": ["ai"],
                },
                {
                    "key": "source-b",
                    "name": "Source B",
                    "feed_url": "https://b.example/feed",
                    "reputation": 0.8,
                    "default_categories": ["ai"],
                },
            ]
        ),
        encoding="utf-8",
    )
    return Settings(
        _env_file=None,
        rss_catalog_path=catalog,
        digest_top_n=1,
        dry_run=dry_run,
    )


def _item(
    source: str,
    external_id: str,
    title: str,
    url: str,
    *,
    reputation: float,
) -> CollectedItem:
    return CollectedItem(
        external_id=external_id,
        source_key=source,
        source_name=source,
        source_reputation=reputation,
        title=title,
        url=url,
        published_at=datetime.now(UTC),
        collected_at=datetime.now(UTC),
        description="A factual AI robotics release with enough context.",
        source_categories=[Category.AI],
    )


@pytest.mark.asyncio
async def test_vertical_slice_deduplicates_selects_top_n_and_reuses_llm_cache(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    repository = _MemoryRepository()
    collector = _Collector(
        {
            "source-a": [
                _item(
                    "source-a",
                    "a-1",
                    "OpenAI releases a new robotics research system today",
                    "https://example.com/story?utm_source=feed-a",
                    reputation=0.9,
                ),
                _item(
                    "source-a",
                    "a-2",
                    "A smaller unrelated AI update",
                    "https://example.com/other",
                    reputation=0.9,
                ),
            ],
            "source-b": [
                _item(
                    "source-b",
                    "b-1",
                    "OpenAI releases a new robotics research system today",
                    "https://example.com/story?utm_source=feed-b",
                    reputation=0.8,
                )
            ],
        }
    )
    enricher = _CountingEnricher()
    notifier = _Notifier()
    service = DigestService(
        settings=settings,
        repository=repository,  # type: ignore[arg-type]
        collector=collector,  # type: ignore[arg-type]
        enricher=enricher,
        notifier=notifier,
    )

    first = await service.run_once()
    second = await service.run_once()

    assert first.collected_count == 3
    assert first.inserted_count == 3
    assert first.duplicate_count == 1
    assert first.candidate_count == 2
    assert first.selected_count == 1
    assert first.delivered_count == 0
    assert second.inserted_count == 0
    assert len(enricher.calls) == 1
    assert len(enricher.calls[0]) == 1
    assert repository.saved_enrichments == 1
    assert repository.recorded_deliveries == []
    assert len(notifier.calls) == 2
    canonical = next(
        material for material in repository.materials if material.duplicate_of_id is None
    )
    assert canonical.independent_mentions == 2


@pytest.mark.asyncio
async def test_pipeline_accepts_hacker_news_and_arxiv_sources_without_network(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "m2-sources.json"
    catalog.write_text(
        json.dumps(
            [
                {
                    "key": "hacker-news",
                    "name": "Hacker News",
                    "kind": "hacker_news",
                    "feed_url": "https://hacker-news.firebaseio.com/v0",
                    "default_categories": [],
                },
                {
                    "key": "arxiv-ai",
                    "name": "arXiv",
                    "kind": "arxiv",
                    "feed_url": "https://export.arxiv.org/api/query",
                    "arxiv_categories": ["cs.AI"],
                    "default_categories": ["research"],
                },
            ]
        ),
        encoding="utf-8",
    )
    settings = Settings(_env_file=None, rss_catalog_path=catalog, digest_top_n=2, dry_run=True)
    repository = _MemoryRepository()
    service = DigestService(
        settings=settings,
        repository=repository,  # type: ignore[arg-type]
        collector=_Collector(
            {
                "hacker-news": [
                    _item(
                        "hacker-news",
                        "42",
                        "Open-source AI model takes Hacker News",
                        "https://example.com/model",
                        reputation=0.8,
                    ).model_copy(update={"popularity": {"points": 900.0, "comments": 100.0}})
                ],
                "arxiv-ai": [
                    _item(
                        "arxiv-ai",
                        "2608.01234",
                        "Research on robots learning manipulation",
                        "https://arxiv.org/abs/2608.01234",
                        reputation=0.95,
                    ).model_copy(update={"source_categories": [Category.RESEARCH]})
                ],
            }
        ),  # type: ignore[arg-type]
        enricher=_CountingEnricher(),
        notifier=_Notifier(),
    )

    summary = await service.run_once()

    assert summary.collected_count == 2
    assert summary.inserted_count == 2
    assert summary.selected_count == 2
    assert {material.item.source_key for material in repository.materials} == {
        "hacker-news",
        "arxiv-ai",
    }
    arxiv_material = next(
        item for item in repository.materials if item.item.source_key == "arxiv-ai"
    )
    assert Category.RESEARCH in arxiv_material.item.categories


@pytest.mark.asyncio
async def test_real_run_records_telegram_delivery(tmp_path: Path) -> None:
    settings = _settings(tmp_path, dry_run=False)
    repository = _MemoryRepository()
    collector = _Collector(
        {
            "source-a": [
                _item(
                    "source-a",
                    "a-1",
                    "A significant new AI system",
                    "https://example.com/significant",
                    reputation=0.9,
                )
            ],
            "source-b": [],
        }
    )
    service = DigestService(
        settings=settings,
        repository=repository,  # type: ignore[arg-type]
        collector=collector,  # type: ignore[arg-type]
        enricher=_CountingEnricher(),
        notifier=_Notifier(),
    )

    summary = await service.run_once()

    assert summary.delivered_count == 1
    assert len(repository.recorded_deliveries) == 1


@pytest.mark.asyncio
async def test_etag_checkpoint_is_not_advanced_when_ingest_fails(tmp_path: Path) -> None:
    class FailingRepository(_MemoryRepository):
        async def add_material(
            self,
            source_id: UUID,
            item: NormalizedItem,
            *,
            duplicate_of_id: UUID | None = None,
        ) -> StoredMaterial:
            del source_id, item, duplicate_of_id
            raise RuntimeError("database write failed")

    settings = _settings(tmp_path)
    repository = FailingRepository()
    collector = _Collector(
        {
            "source-a": [
                _item(
                    "source-a",
                    "a-1",
                    "A significant new AI system",
                    "https://example.com/significant",
                    reputation=0.9,
                )
            ],
            "source-b": [],
        }
    )
    service = DigestService(
        settings=settings,
        repository=repository,  # type: ignore[arg-type]
        collector=collector,  # type: ignore[arg-type]
        enricher=_CountingEnricher(),
        notifier=_Notifier(),
    )

    with pytest.raises(RuntimeError, match="database write failed"):
        await service.run_once()

    assert repository.source_results == []


@pytest.mark.asyncio
async def test_reddit_failure_does_not_block_other_sources(tmp_path: Path) -> None:
    catalog = tmp_path / "sources.json"
    catalog.write_text(
        json.dumps(
            [
                {"key": "rss", "name": "RSS", "feed_url": "https://rss.example/feed"},
                {
                    "key": "reddit-ai",
                    "name": "Reddit AI",
                    "kind": "reddit",
                    "feed_url": "https://www.reddit.com",
                    "reddit_subreddit": "artificial",
                },
            ]
        ),
        encoding="utf-8",
    )

    class RedditFailingCollector(_Collector):
        async def fetch(
            self,
            source: FeedSource,
            *,
            etag: str | None = None,
            last_modified: str | None = None,
        ) -> FeedFetchResult:
            if source.kind == "reddit":
                raise RuntimeError("Reddit OAuth returned HTTP 401")
            return await super().fetch(source, etag=etag, last_modified=last_modified)

    repository = _MemoryRepository()
    service = DigestService(
        settings=Settings(_env_file=None, rss_catalog_path=catalog, digest_top_n=1, dry_run=True),
        repository=repository,  # type: ignore[arg-type]
        collector=RedditFailingCollector(
            {"rss": [_item("rss", "1", "AI update", "https://example.com/ai", reputation=0.8)]}
        ),  # type: ignore[arg-type]
        enricher=_CountingEnricher(),
        notifier=_Notifier(),
    )

    summary = await service.run_once()

    assert summary.status == "completed_with_errors"
    assert summary.inserted_count == 1
    assert summary.source_failures[0].source_key == "reddit-ai"


def test_cached_gpt_cards_are_fifo_delivery_queue_before_new_top_ranked_items() -> None:
    now = datetime.now(UTC)
    old_cached_id = uuid4()
    recent_cached_id = uuid4()
    fresh_id = uuid4()

    def candidate(
        material_id: UUID,
        *,
        collected_at: datetime,
        cached: bool,
    ) -> StoredMaterial:
        raw = _item(
            "source-a",
            str(material_id),
            f"Material {material_id}",
            f"https://example.com/{material_id}",
            reputation=0.9,
        ).model_copy(update={"published_at": collected_at, "collected_at": collected_at})
        normalized = NormalizedItem(
            external_id=raw.external_id,
            source_key=raw.source_key,
            source_name=raw.source_name,
            source_reputation=raw.source_reputation,
            title=raw.title,
            url=raw.url,
            canonical_url=raw.url,
            published_at=collected_at,
            collected_at=collected_at,
            description=raw.description,
            source_categories=raw.source_categories,
            categories=[Category.AI],
            popularity={},
            content_hash=str(material_id),
            normalized_title=raw.title.casefold(),
        )
        return StoredMaterial(
            id=material_id,
            item=normalized,
            llm_enrichment=(
                EditorialEnrichment(
                    title_ru="Готово",
                    summary_ru="Уже обработано GPT.",
                    why_important="Нужно повторить доставку.",
                    post_fit_score=8,
                )
                if cached
                else None
            ),
        )

    candidates = [
        candidate(old_cached_id, collected_at=now - timedelta(hours=3), cached=True),
        candidate(recent_cached_id, collected_at=now - timedelta(hours=2), cached=True),
        candidate(fresh_id, collected_at=now, cached=False),
    ]
    ranked = [
        RankedMaterial(
            material_id=old_cached_id,
            title="old cached",
            url="https://example.com/old",
            source_name="Source A",
            published_at=now - timedelta(hours=3),
            description="",
            categories=[Category.AI],
            popularity={},
            independent_mentions=1,
            score=1.0,
            score_reasons=[],
        ),
        RankedMaterial(
            material_id=recent_cached_id,
            title="recent cached",
            url="https://example.com/recent",
            source_name="Source A",
            published_at=now - timedelta(hours=2),
            description="",
            categories=[Category.AI],
            popularity={},
            independent_mentions=1,
            score=2.0,
            score_reasons=[],
        ),
        RankedMaterial(
            material_id=fresh_id,
            title="high score fresh",
            url="https://example.com/fresh",
            source_name="Source A",
            published_at=now,
            description="",
            categories=[Category.AI],
            popularity={},
            independent_mentions=4,
            score=99.0,
            score_reasons=[],
        ),
    ]

    selected = _select_for_delivery(ranked, candidates, top_n=2)

    assert [material.material_id for material in selected] == [old_cached_id, recent_cached_id]
