from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from f117.adapters.collectors import SourceCollector
from f117.adapters.openai_editorial import EditorialEnricher
from f117.adapters.rss import FeedFetchResult
from f117.adapters.telegram import DeliveryReceipt, DigestNotifier
from f117.config import Settings
from f117.domain import EditorialCard, RankedMaterial, StoredMaterial
from f117.pipeline.classifier import classify_item
from f117.pipeline.deduplicator import find_duplicate
from f117.pipeline.normalizer import normalize_item
from f117.pipeline.ranking import RankingConfig, score_material
from f117.storage.repository import Repository, SourceState

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SourceFailure:
    source_key: str
    error: str


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: UUID
    status: str
    collected_count: int
    inserted_count: int
    duplicate_count: int
    candidate_count: int
    selected_count: int
    delivered_count: int
    editorial_failure_count: int
    source_failures: tuple[SourceFailure, ...] = ()


@dataclass(frozen=True, slots=True)
class _SourceResult:
    state: SourceState
    result: FeedFetchResult | None
    failure: SourceFailure | None


class DigestService:
    """One complete configured-source-to-Telegram digest run."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: Repository,
        collector: SourceCollector,
        enricher: EditorialEnricher,
        notifier: DigestNotifier,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.collector = collector
        self.enricher = enricher
        self.notifier = notifier
        self.fetch_semaphore = asyncio.Semaphore(settings.rss_max_concurrency)
        self.ranking_config = RankingConfig(
            freshness_weight=settings.rank_freshness_weight,
            reputation_weight=settings.rank_reputation_weight,
            mentions_weight=settings.rank_mentions_weight,
            popularity_weight=settings.rank_popularity_weight,
            growth_weight=settings.rank_growth_weight,
            novelty_weight=settings.rank_novelty_weight,
            topic_fit_weight=settings.rank_topic_fit_weight,
            unusualness_weight=settings.rank_unusualness_weight,
            freshness_half_life_hours=settings.rank_freshness_half_life_hours,
            full_mentions=settings.rank_full_mentions,
        )

    async def run_once(self) -> RunSummary:
        run_id = await self.repository.create_digest_run(dry_run=self.settings.dry_run)
        counts = {
            "collected_count": 0,
            "inserted_count": 0,
            "duplicate_count": 0,
            "candidate_count": 0,
            "selected_count": 0,
            "delivered_count": 0,
            "editorial_failure_count": 0,
        }
        failures: list[SourceFailure] = []

        try:
            source_states = await self.repository.sync_sources(self.settings.load_feed_sources())
            source_results = await asyncio.gather(
                *(self._fetch_source(state) for state in source_states)
            )
            recent = await self.repository.recent_materials(days=self.settings.dedup_lookback_days)

            for source_result in source_results:
                if source_result.failure is not None:
                    failures.append(source_result.failure)
                    continue
                if source_result.result is None:
                    continue

                counts["collected_count"] += len(source_result.result.items)
                for collected in source_result.result.items:
                    if await self.repository.has_material(
                        source_result.state.id, collected.external_id
                    ):
                        await self.repository.refresh_observation(
                            source_result.state.id,
                            collected.external_id,
                            dict(collected.popularity),
                        )
                        continue
                    try:
                        normalized = classify_item(normalize_item(collected))
                    except (TypeError, ValueError) as exc:
                        logger.warning(
                            "Rejected item from %s (%s): %s",
                            source_result.state.source.key,
                            collected.external_id,
                            exc,
                        )
                        continue

                    duplicate = find_duplicate(
                        normalized,
                        recent,
                        threshold=self.settings.dedup_title_threshold,
                    )
                    duplicate_of_id: UUID | None = None
                    if duplicate is not None:
                        duplicate_of_id = duplicate.duplicate_of_id or duplicate.id
                        counts["duplicate_count"] += 1

                    stored = await self.repository.add_material(
                        source_result.state.id,
                        normalized,
                        duplicate_of_id=duplicate_of_id,
                    )
                    recent.append(stored)
                    counts["inserted_count"] += 1

                # Persist the conditional-fetch checkpoint only after every item
                # in this response has been durably accepted or rejected.
                await self.repository.record_source_result(
                    source_result.state.id,
                    etag=source_result.result.etag,
                    last_modified=source_result.result.last_modified,
                    success=True,
                )

            candidates = await self.repository.digest_candidates(
                lookback_hours=self.settings.candidate_lookback_hours
            )
            counts["candidate_count"] = len(candidates)
            ranked = [
                score_material(candidate, config=self.ranking_config) for candidate in candidates
            ]
            await self.repository.save_rankings(ranked)
            selected = _select_for_delivery(
                ranked,
                candidates,
                top_n=self.settings.digest_top_n,
            )
            counts["selected_count"] = len(selected)

            cards = await self._editorial_cards(selected, candidates)
            counts["editorial_failure_count"] = sum(
                card.editorial_error is not None for card in cards
            )
            if cards and self.settings.dry_run:
                await self.notifier.send(cards)
            elif cards:
                cards = [card for card in cards if card.editorial_error is None]
                recorded_ids: set[UUID] = set()

                async def record_delivery(receipt: DeliveryReceipt) -> None:
                    await self.repository.record_deliveries(
                        run_id,
                        [(receipt.material_id, receipt.message_id)],
                    )
                    recorded_ids.add(receipt.material_id)
                    counts["delivered_count"] += 1

                receipts = (
                    await self.notifier.send(cards, on_delivered=record_delivery) if cards else []
                )
                for receipt in receipts:
                    if receipt.material_id not in recorded_ids:
                        await record_delivery(receipt)

            has_errors = bool(failures) or counts["editorial_failure_count"] > 0
            status = "completed_with_errors" if has_errors else "completed"
            run_error = (
                f"{counts['editorial_failure_count']} materials await OpenAI retry"
                if counts["editorial_failure_count"]
                else None
            )
            await self.repository.finish_digest_run(
                run_id,
                status=status,
                collected_count=counts["collected_count"],
                inserted_count=counts["inserted_count"],
                duplicate_count=counts["duplicate_count"],
                candidate_count=counts["candidate_count"],
                selected_count=counts["selected_count"],
                delivered_count=counts["delivered_count"],
                editorial_failure_count=counts["editorial_failure_count"],
                error=run_error,
            )
            return RunSummary(
                run_id=run_id,
                status=status,
                collected_count=counts["collected_count"],
                inserted_count=counts["inserted_count"],
                duplicate_count=counts["duplicate_count"],
                candidate_count=counts["candidate_count"],
                selected_count=counts["selected_count"],
                delivered_count=counts["delivered_count"],
                editorial_failure_count=counts["editorial_failure_count"],
                source_failures=tuple(failures),
            )
        except Exception as exc:
            logger.exception("Digest run %s failed", run_id)
            await self.repository.finish_digest_run(
                run_id,
                status="failed",
                error=str(exc),
                collected_count=counts["collected_count"],
                inserted_count=counts["inserted_count"],
                duplicate_count=counts["duplicate_count"],
                candidate_count=counts["candidate_count"],
                selected_count=counts["selected_count"],
                delivered_count=counts["delivered_count"],
                editorial_failure_count=counts["editorial_failure_count"],
            )
            raise

    async def _fetch_source(self, state: SourceState) -> _SourceResult:
        try:
            async with self.fetch_semaphore:
                result = await self.collector.fetch(
                    state.source,
                    etag=state.etag,
                    last_modified=state.last_modified,
                )
        except Exception as exc:
            error = str(exc)
            logger.warning("Source %s failed: %s", state.source.key, error)
            await self.repository.record_source_result(
                state.id,
                etag=state.etag,
                last_modified=state.last_modified,
                success=False,
                error=error,
            )
            return _SourceResult(
                state=state,
                result=None,
                failure=SourceFailure(source_key=state.source.key, error=error),
            )

        return _SourceResult(state=state, result=result, failure=None)

    async def _editorial_cards(
        self,
        selected: Sequence[RankedMaterial],
        candidates: Sequence[StoredMaterial],
    ) -> list[EditorialCard]:
        candidate_by_id = {candidate.id: candidate for candidate in candidates}
        card_by_id: dict[UUID, EditorialCard] = {}
        missing: list[RankedMaterial] = []

        for material in selected:
            stored = candidate_by_id[material.material_id]
            if stored.llm_enrichment is None:
                missing.append(material)
                continue
            card_by_id[material.material_id] = EditorialCard(
                material=material,
                enrichment=stored.llm_enrichment,
                llm_model=stored.llm_model,
                usage=stored.llm_usage,
            )

        enriched = await self.enricher.enrich(missing) if missing else []
        for card in enriched:
            card_by_id[card.material.material_id] = card
            if card.llm_model is not None:
                await self.repository.save_enrichment(
                    card.material.material_id,
                    card.enrichment,
                    model=card.llm_model,
                    usage=card.usage,
                )

        return [card_by_id[material.material_id] for material in selected]


def _select_for_delivery(
    ranked: Sequence[RankedMaterial],
    candidates: Sequence[StoredMaterial],
    *,
    top_n: int,
) -> list[RankedMaterial]:
    """Reserve the delivery queue before choosing fresh editorial candidates.

    A material with persisted LLM enrichment has already consumed a paid request.
    It is retried first, FIFO by first sighting, so a burst of new RSS entries
    can never starve an unfinished Telegram delivery.
    """

    candidate_by_id = {candidate.id: candidate for candidate in candidates}
    retry_queue = sorted(
        (
            material
            for material in ranked
            if candidate_by_id[material.material_id].llm_enrichment is not None
        ),
        key=lambda material: (
            candidate_by_id[material.material_id].item.collected_at,
            material.material_id,
        ),
    )
    available_slots = max(0, top_n - len(retry_queue))
    fresh = sorted(
        (
            material
            for material in ranked
            if candidate_by_id[material.material_id].llm_enrichment is None
        ),
        key=lambda material: (material.score, material.published_at),
        reverse=True,
    )
    return retry_queue[:top_n] + fresh[:available_slots]
