from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from f117.adapters.collectors import SourceCollector
from f117.adapters.media import MetadataMediaFetcher
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
from f117.domain import Category, EditorialCard, RankedMaterial, StoredMaterial
from f117.pipeline.classifier import classify_item
from f117.pipeline.deduplicator import duplicate_reason, find_duplicate
from f117.pipeline.discovery import DiscoveryConfig, assess_discovery
from f117.pipeline.diversity import DiversityConfig, diversify, soft_balance_cyberculture
from f117.pipeline.editorial import EditorialConfig, assess_editorial_fit
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
        self.editorial_config = EditorialConfig(
            fit_weight=settings.editorial_fit_weight,
            minimum_fit=settings.minimum_editorial_fit,
            minimum_delivery_score=settings.minimum_delivery_score,
            github_min_stars=settings.github_delivery_min_stars,
            github_min_star_velocity=settings.github_delivery_min_star_velocity,
            github_min_forks=settings.github_delivery_min_forks,
            github_min_mentions=settings.github_delivery_min_mentions,
            github_exceptional_fit=settings.github_exceptional_editorial_fit,
            arxiv_min_fit=settings.arxiv_min_editorial_fit,
            youtube_min_views=settings.youtube_delivery_min_views,
            youtube_min_view_velocity=settings.youtube_delivery_min_view_velocity,
            youtube_min_likes=settings.youtube_delivery_min_likes,
            youtube_min_mentions=settings.youtube_delivery_min_mentions,
            reddit_min_upvotes=settings.reddit_delivery_min_upvotes,
            reddit_min_comments=settings.reddit_delivery_min_comments,
            reddit_min_velocity=settings.reddit_delivery_min_velocity,
            reddit_min_mentions=settings.reddit_delivery_min_mentions,
            reddit_min_fit=settings.reddit_min_editorial_fit,
            reddit_rss_min_fit=settings.reddit_rss_min_editorial_fit,
            reddit_rss_exceptional_fit=settings.reddit_rss_exceptional_editorial_fit,
            urgent_min_fit=settings.urgent_min_editorial_fit,
            urgent_min_delivery_score=settings.urgent_min_delivery_score,
        )
        self.media_fetcher = MetadataMediaFetcher(
            timeout_seconds=settings.metadata_fetch_timeout_seconds,
            max_response_bytes=settings.metadata_fetch_max_response_bytes,
            user_agent=settings.http_user_agent,
        )

    async def run_once(
        self,
        *,
        collect: bool = True,
        delivery_mode: Literal["digest", "urgent", "none"] = "digest",
    ) -> RunSummary:
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
            return await self._run_once_owned(collect=collect, delivery_mode=delivery_mode)

    async def _run_once_owned(
        self,
        *,
        collect: bool,
        delivery_mode: Literal["digest", "urgent", "none"],
    ) -> RunSummary:
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
            if collect and not self.settings.dry_run:
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
                lookback_hours=self.settings.candidate_lookback_hours,
                delivery_claim_lease_seconds=self.settings.delivery_claim_lease_seconds,
            )
            candidates = [
                candidate for candidate in candidates if self._passes_base_freshness(candidate)
            ]
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
            candidate_by_id = {candidate.id: candidate for candidate in candidates}
            editorial = {
                material.material_id: assess_editorial_fit(
                    candidate_by_id[material.material_id],
                    material,
                    config=self.editorial_config,
                )
                for material in ranked
            }
            ranked = [
                material.model_copy(
                    update={
                        "editorial_fit": editorial[material.material_id].fit,
                        "editorial_reasons": editorial[material.material_id].reasons,
                        "delivery_score": editorial[material.material_id].delivery_score,
                        "urgent": editorial[material.material_id].urgent,
                        "score_reasons": [
                            *material.score_reasons,
                            *(
                                f"editorial: {reason}"
                                for reason in editorial[material.material_id].reasons
                            ),
                        ],
                    }
                )
                for material in ranked
            ]
            ranked = [
                material
                for material in ranked
                if self._passes_discovery_freshness(candidate_by_id[material.material_id], material)
            ]
            await self.repository.save_rankings(ranked)
            await self.repository.save_discovery_scores(
                {material.material_id: material.discovery_score for material in ranked}
            )
            selected: list[RankedMaterial] = []
            if delivery_mode != "none":
                selected = _select_for_delivery(
                    ranked,
                    candidates,
                    top_n=(
                        self.settings.urgent_max_items
                        if delivery_mode == "urgent"
                        else self.settings.effective_digest_max_items
                    ),
                    discovery_selection_boost=self.settings.discovery_selection_boost,
                    editorial_retry_slots=self.settings.editorial_retry_slots,
                    diversity_config=DiversityConfig(
                        max_per_source=self.settings.diversity_max_per_source,
                        max_per_entity=self.settings.diversity_max_per_entity,
                        max_per_category=self.settings.diversity_max_per_category,
                        strong_score_threshold=self.settings.diversity_strong_score_threshold,
                        discovery_selection_boost=self.settings.discovery_selection_boost,
                        editorial_fit_weight=self.settings.editorial_fit_weight,
                    ),
                    minimum_delivery_score=self.settings.minimum_delivery_score,
                    minimum_editorial_fit=self.settings.minimum_editorial_fit,
                    editorial_fit_weight=self.settings.editorial_fit_weight,
                    urgent_only=delivery_mode == "urgent",
                )
            selected = await self._enrich_final_media(selected)
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
                    if not await self.repository.begin_delivery(
                        material_id,
                        lease_seconds=self.settings.delivery_claim_lease_seconds,
                    ):
                        raise RuntimeError(
                            f"Material {material_id} is no longer available for delivery"
                        )

                async def record_delivery(receipt: DeliveryReceipt) -> None:
                    try:
                        await self.repository.record_deliveries(
                            run_id,
                            [(receipt.material_id, receipt.message_id)],
                        )
                    except Exception as exc:
                        # Telegram has already returned a receipt, so a failed
                        # persistence attempt must never fall back to a stale
                        # pre-request lease and cause an automatic resend.
                        await self.repository.mark_delivery_ambiguous(
                            receipt.material_id,
                            error=f"Telegram receipt persistence failed: {exc}",
                        )
                        raise
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
                    elif exc.material_id is not None:
                        await self.repository.mark_delivery_ambiguous(
                            exc.material_id, error=str(exc)
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

    def _passes_base_freshness(self, material: StoredMaterial) -> bool:
        """Keep old content out of daily sections; GitHub freshness comes from signals/releases."""

        if material.item.source_key.startswith("github-"):
            return True
        age_days = (datetime.now(UTC) - material.item.published_at).total_seconds() / 86_400
        categories = set(material.item.categories)
        if Category.FUNNY in categories or Category.WTF in categories:
            return age_days <= self.settings.freshness_funny_wtf_max_age_days
        if material.item.source_key.startswith("youtube-"):
            return age_days <= self.settings.freshness_youtube_daily_max_age_days
        return age_days <= self.settings.freshness_daily_max_age_days

    def _passes_discovery_freshness(self, stored: StoredMaterial, ranked: RankedMaterial) -> bool:
        if stored.item.source_key.startswith("github-") or not (ranked.rising or ranked.hidden_gem):
            return True
        age_days = (datetime.now(UTC) - stored.item.published_at).total_seconds() / 86_400
        return age_days <= self.settings.freshness_discovery_max_age_days

    async def _enrich_final_media(self, selected: Sequence[RankedMaterial]) -> list[RankedMaterial]:
        """Fetch one page at most per final card, never during collection or dry-runs."""

        if self.settings.dry_run:
            return list(selected)
        enriched: list[RankedMaterial] = []
        for material in selected:
            if material.media_type != "none" or material.thumbnail_url:
                enriched.append(material)
                continue
            image = await self.media_fetcher.image_for(material.url)
            if image is None:
                enriched.append(material)
                continue
            updated = material.model_copy(
                update={
                    "media_type": "image",
                    "media_url": image,
                    "thumbnail_url": image,
                    "media_source": "page:og",
                }
            )
            await self.repository.update_media(
                material.material_id,
                media_type="image",
                media_url=image,
                thumbnail_url=image,
                media_source="page:og",
            )
            enriched.append(updated)
        return enriched


def _select_for_delivery(
    ranked: Sequence[RankedMaterial],
    candidates: Sequence[StoredMaterial],
    *,
    top_n: int,
    discovery_selection_boost: float = 0.0,
    editorial_retry_slots: int = 2,
    diversity_config: DiversityConfig | None = None,
    minimum_delivery_score: float = 0.0,
    minimum_editorial_fit: float = 0.0,
    editorial_fit_weight: float = 0.0,
    urgent_only: bool = False,
) -> list[RankedMaterial]:
    """Reserve the delivery queue before choosing fresh editorial candidates.

    A material with persisted LLM enrichment has already consumed a paid request.
    It is retried first, FIFO by first sighting, so a burst of new RSS entries
    can never starve an unfinished Telegram delivery.
    """

    candidate_by_id = {candidate.id: candidate for candidate in candidates}
    ranked = [
        material
        for material in ranked
        if material.delivery_score >= minimum_delivery_score
        and material.editorial_fit >= minimum_editorial_fit
        and (not urgent_only or material.urgent)
    ]
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
    now = datetime.now(UTC)
    editorial_retries = sorted(
        (
            material
            for material in ranked
            if candidate_by_id[material.material_id].llm_enrichment is None
            and _is_due_editorial_retry(candidate_by_id[material.material_id], now)
        ),
        key=lambda material: (
            _editorial_retry_at(candidate_by_id[material.material_id]),
            candidate_by_id[material.material_id].item.collected_at,
            material.material_id,
        ),
    )
    fresh = sorted(
        (
            material
            for material in ranked
            if candidate_by_id[material.material_id].llm_enrichment is None
            and material not in editorial_retries
        ),
        key=lambda material: (
            _selection_score(
                material,
                candidate_by_id[material.material_id],
                discovery_selection_boost,
                editorial_fit_weight,
            ),
            material.score,
            material.published_at,
        ),
        reverse=True,
    )
    if diversity_config is not None:
        fresh = diversify(fresh, config=diversity_config)
        fresh = soft_balance_cyberculture(fresh, top_n=top_n, config=diversity_config)

    # Reserve a fresh slot against both retry queues together. Cached delivery
    # retries remain first in FIFO order, while editorial retries consume only
    # their bounded FIFO quota from the remaining priority capacity.
    if top_n == 1:
        # Keep the single-card order: cached delivery work first, then fresh
        # material, and a due editorial retry only when it is the sole choice.
        cached_retries = retry_queue[:1]
        reserved_retries: list[RankedMaterial] = []
    else:
        priority_capacity = top_n - 1 if fresh else top_n
        cached_retries = retry_queue[:priority_capacity]
        editorial_capacity = max(0, priority_capacity - len(cached_retries))
        reserved_retries = editorial_retries[: min(editorial_retry_slots, editorial_capacity)]
    selected = cached_retries + reserved_retries
    selected += fresh[: max(0, top_n - len(selected))]
    if top_n == 1 and not selected and editorial_retry_slots > 0 and editorial_retries:
        return [editorial_retries[0]]
    return selected


def _selection_score(
    material: RankedMaterial,
    candidate: StoredMaterial,
    discovery_selection_boost: float,
    editorial_fit_weight: float = 0.0,
) -> float:
    """Keep viral unrelated material from buying a TOP position via discovery alone."""

    base = material.score
    if editorial_fit_weight:
        base = (
            material.score * (1.0 - editorial_fit_weight)
            + material.editorial_fit * editorial_fit_weight
        )
    boost = discovery_selection_boost
    if not candidate.item.categories or set(candidate.item.categories) == {Category.OTHER}:
        boost *= 0.20
    return base + material.discovery_score * boost


def _is_due_editorial_retry(material: StoredMaterial, now: datetime) -> bool:
    retry_at = material.editorial_retry_at
    return retry_at is not None and retry_at <= now


def _editorial_retry_at(material: StoredMaterial) -> datetime:
    retry_at = material.editorial_retry_at
    assert retry_at is not None
    return retry_at
