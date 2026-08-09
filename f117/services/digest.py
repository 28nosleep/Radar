from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from f117.adapters.collectors import SourceCollector
from f117.adapters.openai_editorial import DeterministicEditorialEnricher, EditorialEnricher
from f117.adapters.rss import FeedFetchResult
from f117.adapters.telegram import (
    DeliveryReceipt,
    DigestNotifier,
    TelegramError,
    render_card,
    render_digest,
)
from f117.config import Settings
from f117.domain import EditorialCard, RankedMaterial, StoredMaterial
from f117.pipeline.classifier import classify_item
from f117.pipeline.deduplicator import duplicate_reason, find_duplicate
from f117.pipeline.discovery import DiscoveryConfig, assess_discovery
from f117.pipeline.diversity import DiversityConfig, diversify
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
        self.discovery_config = DiscoveryConfig(
            growth_weight=settings.discovery_growth_weight,
            acceleration_weight=settings.discovery_acceleration_weight,
            diversity_weight=settings.discovery_diversity_weight,
            novelty_weight=settings.discovery_novelty_weight,
            freshness_weight=settings.discovery_freshness_weight,
            min_baseline=settings.discovery_min_baseline,
            min_growth_absolute=settings.discovery_min_growth_absolute,
            hidden_gem_max_popularity=settings.discovery_hidden_gem_max_popularity,
        )

    async def run_once(self) -> RunSummary:
        async with self.repository.run_lock() as acquired:
            if not acquired:
                logger.info("Skipped digest run because another Radar owner holds the lock")
                return RunSummary(
                    run_id=uuid4(),
                    status="skipped_already_running",
                    collected_count=0,
                    inserted_count=0,
                    duplicate_count=0,
                    candidate_count=0,
                    selected_count=0,
                    delivered_count=0,
                    editorial_failure_count=0,
                )
            return await self._run_once_owned()

    async def _run_once_owned(self) -> RunSummary:
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
            source_results: Sequence[_SourceResult] = ()
            recent: list[StoredMaterial] = []
            if not self.settings.dry_run:
                source_states = await self.repository.sync_sources(
                    self.settings.load_feed_sources()
                )
                source_results = await asyncio.gather(
                    *(self._fetch_source(state) for state in source_states)
                )
                recent = await self.repository.recent_materials(
                    days=self.settings.dedup_lookback_days
                )

            for source_result in source_results:
                if source_result.failure is not None:
                    failures.append(source_result.failure)
                    continue
                if source_result.result is None:
                    continue

                counts["collected_count"] += len(source_result.result.items)
                for collected in source_result.result.items:
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

                    if await self.repository.has_material(
                        source_result.state.id, normalized.external_id
                    ):
                        await self.repository.refresh_material(source_result.state.id, normalized)
                        continue

                    duplicate = await self.repository.material_by_canonical_url(
                        normalized.canonical_url
                    )
                    if (
                        duplicate is not None
                        and duplicate_reason(normalized, duplicate.item) is None
                    ):
                        duplicate = None
                    if duplicate is None:
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
                # A capped RSS feed has intentionally left entries unprocessed;
                # retaining the old checkpoint prevents an ETag advance from
                # making those entries unreachable on the next fetch.
                await self.repository.record_source_result(
                    source_result.state.id,
                    etag=(
                        source_result.state.etag
                        if source_result.result.partial
                        else source_result.result.etag
                    ),
                    last_modified=(
                        source_result.state.last_modified
                        if source_result.result.partial
                        else source_result.result.last_modified
                    ),
                    success=True,
                )

            candidates = await self.repository.digest_candidates(
                lookback_hours=self.settings.candidate_lookback_hours
            )
            counts["candidate_count"] = len(candidates)
            ranked = [
                score_material(candidate, config=self.ranking_config) for candidate in candidates
            ]
            histories = await self.repository.metric_histories(
                [candidate.id for candidate in candidates]
            )
            now = datetime.now(UTC)
            assessed = {
                candidate.id: assess_discovery(
                    candidate,
                    histories.get(candidate.id, []),
                    now=now,
                    config=self.discovery_config,
                )
                for candidate in candidates
            }
            ranked = [
                material.model_copy(
                    update={
                        "discovery_score": assessed[material.material_id].score,
                        "discovery_reasons": assessed[material.material_id].reasons,
                        "hidden_gem": assessed[material.material_id].hidden_gem,
                        "rising": assessed[material.material_id].score
                        >= self.settings.discovery_rising_threshold,
                    }
                )
                for material in ranked
            ]
            await self.repository.save_rankings(ranked)
            await self.repository.save_discovery_scores(
                {material.material_id: material.discovery_score for material in ranked}
            )
            selected = _select_for_delivery(
                ranked,
                candidates,
                top_n=self.settings.digest_top_n,
                discovery_selection_boost=self.settings.discovery_selection_boost,
                diversity_config=DiversityConfig(
                    max_per_source=self.settings.diversity_max_per_source,
                    max_per_entity=self.settings.diversity_max_per_entity,
                    max_per_category=self.settings.diversity_max_per_category,
                    strong_score_threshold=self.settings.diversity_strong_score_threshold,
                    discovery_selection_boost=self.settings.discovery_selection_boost,
                ),
            )
            counts["selected_count"] = len(selected)
            await self.repository.record_selection([material.material_id for material in selected])

            cards, editorial_failures = await self._editorial_cards(selected, candidates)
            cards, render_failures = await self._renderable_cards(cards)
            counts["editorial_failure_count"] = editorial_failures + render_failures
            if cards and self.settings.dry_run:
                # Keep dry-runs entirely local: no collector, OpenAI, Telegram
                # notifier, or feedback poller is touched.
                print(render_digest(cards))
            elif cards:
                recorded_ids: set[UUID] = set()

                async def mark_delivery_started(material_id: UUID) -> None:
                    if not await self.repository.begin_delivery(material_id):
                        raise RuntimeError(
                            f"Material {material_id} is no longer available for delivery"
                        )

                async def record_delivery(receipt: DeliveryReceipt) -> None:
                    await self.repository.record_deliveries(
                        run_id,
                        [(receipt.material_id, receipt.message_id)],
                    )
                    recorded_ids.add(receipt.material_id)
                    counts["delivered_count"] += 1

                try:
                    receipts = await self.notifier.send(
                        cards,
                        on_delivering=mark_delivery_started,
                        on_delivered=record_delivery,
                    )
                except TelegramError as exc:
                    # Telegram's explicit 429 response means the card was not sent;
                    # release only the currently claimed card for a bounded retry.
                    if exc.material_id is not None and not exc.ambiguous:
                        await self.repository.release_delivery_for_retry(
                            exc.material_id,
                            error=str(exc),
                            retry_after_seconds=exc.retry_after_seconds or 60,
                        )
                    raise
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
    ) -> tuple[list[EditorialCard], int]:
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

        failures = 0
        enricher = DeterministicEditorialEnricher() if self.settings.dry_run else self.enricher
        for material in missing:
            card = (await enricher.enrich([material]))[0]
            stored = candidate_by_id[material.material_id]
            if card.editorial_error is not None:
                failures += 1
                attempts_after_this_run = stored.editorial_attempts + 1
                retry_delay = (
                    None
                    if attempts_after_this_run >= self.settings.editorial_max_attempts
                    else min(
                        self.settings.editorial_retry_base_seconds * 2**stored.editorial_attempts,
                        self.settings.editorial_retry_max_seconds,
                    )
                )
                await self.repository.record_editorial_failure(
                    material.material_id,
                    error=card.editorial_error,
                    retry_delay_seconds=retry_delay,
                )
                continue
            card_by_id[material.material_id] = card
            if card.llm_model is not None:
                await self.repository.save_enrichment(
                    card.material.material_id,
                    card.enrichment,
                    model=card.llm_model,
                    usage=card.usage,
                )

        return (
            [
                card_by_id[material.material_id]
                for material in selected
                if material.material_id in card_by_id
            ],
            failures,
        )

    async def _renderable_cards(
        self, cards: Sequence[EditorialCard]
    ) -> tuple[list[EditorialCard], int]:
        """Reject poison cards before Telegram sees the batch header or any card."""

        valid: list[EditorialCard] = []
        failures = 0
        for card in cards:
            try:
                render_card(card, debug=self.settings.telegram_format == "debug")
            except (TypeError, ValueError) as exc:
                failures += 1
                await self.repository.record_editorial_failure(
                    card.material.material_id,
                    error=f"Telegram render: {exc}",
                    retry_delay_seconds=None,
                )
                logger.warning(
                    "Quarantined unrenderable material %s: %s", card.material.material_id, exc
                )
            else:
                valid.append(card)
        return valid, failures


def _select_for_delivery(
    ranked: Sequence[RankedMaterial],
    candidates: Sequence[StoredMaterial],
    *,
    top_n: int,
    discovery_selection_boost: float = 0.0,
    diversity_config: DiversityConfig | None = None,
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
        key=lambda material: (
            material.score + material.discovery_score * discovery_selection_boost,
            material.score,
            material.published_at,
        ),
        reverse=True,
    )
    if diversity_config is not None:
        fresh = diversify(fresh, config=diversity_config)
    return retry_queue[:top_n] + fresh[:available_slots]
