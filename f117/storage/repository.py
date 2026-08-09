from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, or_, select, text, update
from sqlalchemy.orm import selectinload

from f117.domain import (
    Category,
    EditorialEnrichment,
    FeedbackType,
    FeedSource,
    MaterialFeedback,
    MetricSnapshot,
    NormalizedItem,
    RankedMaterial,
    StoredMaterial,
)
from f117.storage.database import Database
from f117.storage.models import (
    DeliveryModel,
    DigestRunModel,
    FeedbackModel,
    MaterialModel,
    MetricSnapshotModel,
    SourceModel,
    TelegramUpdateModel,
)


@dataclass(frozen=True, slots=True)
class SourceState:
    id: UUID
    source: FeedSource
    etag: str | None
    last_modified: str | None


class Repository:
    _RUN_LOCK_KEY = 1_170_001

    def __init__(self, database: Database) -> None:
        self.database = database

    @asynccontextmanager
    async def run_lock(self) -> AsyncIterator[bool]:
        """Hold one PostgreSQL session advisory lock for an entire Radar run."""

        async with self.database.session() as session:
            acquired = bool(
                await session.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": self._RUN_LOCK_KEY}
                )
            )
            try:
                yield acquired
            finally:
                if acquired:
                    await session.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": self._RUN_LOCK_KEY}
                    )

    async def sync_sources(self, sources: list[FeedSource]) -> list[SourceState]:
        async with self.database.session() as session:
            existing = {row.key: row for row in (await session.scalars(select(SourceModel))).all()}
            configured_keys = {source.key for source in sources}
            for source in sources:
                row = existing.get(source.key)
                values = {
                    "name": source.name,
                    "feed_url": str(source.feed_url),
                    "site_url": str(source.site_url) if source.site_url else None,
                    "reputation": source.reputation,
                    "enabled": source.enabled,
                    "default_categories": [value.value for value in source.default_categories],
                }
                if row is None:
                    row = SourceModel(key=source.key, **values)
                    session.add(row)
                    existing[source.key] = row
                else:
                    for key, value in values.items():
                        setattr(row, key, value)

            for key, row in existing.items():
                if key not in configured_keys:
                    row.enabled = False

            await session.commit()
            return [
                SourceState(
                    id=existing[source.key].id,
                    source=source,
                    etag=existing[source.key].etag,
                    last_modified=existing[source.key].last_modified,
                )
                for source in sources
                if source.enabled
            ]

    async def record_source_result(
        self,
        source_id: UUID,
        *,
        etag: str | None,
        last_modified: str | None,
        success: bool,
        error: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        values: dict[str, Any] = {
            "last_checked_at": now,
            "last_error": error,
        }
        if success:
            values.update(
                {
                    "last_success_at": now,
                    "etag": etag,
                    "last_modified": last_modified,
                }
            )
        async with self.database.session() as session:
            statement = update(SourceModel).where(SourceModel.id == source_id).values(**values)
            await session.execute(statement)
            await session.commit()

    async def has_material(self, source_id: UUID, external_id: str) -> bool:
        async with self.database.session() as session:
            query = select(MaterialModel.id).where(
                MaterialModel.source_id == source_id,
                MaterialModel.external_id == external_id,
            )
            return (await session.scalar(query)) is not None

    async def recent_materials(self, *, days: int) -> list[StoredMaterial]:
        since = datetime.now(UTC) - timedelta(days=days)
        async with self.database.session() as session:
            query = (
                select(MaterialModel)
                .where(MaterialModel.published_at >= since)
                .options(selectinload(MaterialModel.source))
                .order_by(MaterialModel.published_at.desc())
            )
            rows = (await session.scalars(query)).all()
            return [self._to_stored(row) for row in rows]

    async def material_by_canonical_url(self, canonical_url: str) -> StoredMaterial | None:
        """Find an exact URL duplicate without applying the fuzzy recency window."""

        async with self.database.session() as session:
            row = await session.scalar(
                select(MaterialModel)
                .where(MaterialModel.canonical_url == canonical_url)
                .options(selectinload(MaterialModel.source))
                .order_by(MaterialModel.collected_at.desc())
            )
            return self._to_stored(row) if row is not None else None

    async def add_material(
        self,
        source_id: UUID,
        item: NormalizedItem,
        *,
        duplicate_of_id: UUID | None = None,
    ) -> StoredMaterial:
        async with self.database.session() as session:
            source = await session.get(SourceModel, source_id)
            if source is None:
                raise LookupError(f"Unknown source: {source_id}")
            try:
                should_increment_mentions = False
                if duplicate_of_id is not None:
                    represented_keys = (
                        await session.scalars(
                            select(SourceModel.key)
                            .join(MaterialModel, MaterialModel.source_id == SourceModel.id)
                            .where(
                                or_(
                                    MaterialModel.id == duplicate_of_id,
                                    MaterialModel.duplicate_of_id == duplicate_of_id,
                                )
                            )
                        )
                    ).all()
                    should_increment_mentions = _provider_family(source.key) not in {
                        _provider_family(key) for key in represented_keys
                    }

                row = MaterialModel(
                    source=source,
                    external_id=item.external_id,
                    title=item.title,
                    url=item.url,
                    canonical_url=item.canonical_url,
                    published_at=item.published_at,
                    collected_at=item.collected_at,
                    description=item.description,
                    author=item.author,
                    categories=[category.value for category in item.categories],
                    popularity=item.popularity,
                    raw_metrics=item.popularity,
                    content_hash=item.content_hash,
                    normalized_title=item.normalized_title,
                    duplicate_of_id=duplicate_of_id,
                )
                session.add(row)
                if should_increment_mentions:
                    await session.execute(
                        update(MaterialModel)
                        .where(MaterialModel.id == duplicate_of_id)
                        .values(
                            independent_mentions=MaterialModel.independent_mentions + 1,
                            last_signal_at=datetime.now(UTC),
                        )
                    )
                await session.flush()
                if item.popularity:
                    session.add(
                        MetricSnapshotModel(
                            material_id=row.id,
                            captured_at=datetime.now(UTC),
                            metrics=dict(item.popularity),
                        )
                    )
                    await self._aggregate_root_metrics(
                        session, duplicate_of_id or row.id, datetime.now(UTC)
                    )
                stored = self._to_stored(row)
                await session.commit()
                return stored
            except Exception:
                await session.rollback()
                raise

    async def refresh_observation(
        self, source_id: UUID, external_id: str, metrics: dict[str, float]
    ) -> None:
        """Persist a metrics-only observation for callers without refreshed content."""

        if not metrics:
            return
        captured_at = datetime.now(UTC)
        async with self.database.session() as session:
            row = await session.scalar(
                select(MaterialModel).where(
                    MaterialModel.source_id == source_id, MaterialModel.external_id == external_id
                )
            )
            if row is None:
                return
            await self._record_metrics(session, row, metrics, captured_at)
            await session.commit()

    async def refresh_material(self, source_id: UUID, item: NormalizedItem) -> None:
        """Refresh mutable content and metrics for a repeated source/external item."""

        captured_at = datetime.now(UTC)
        async with self.database.session() as session:
            row = await session.scalar(
                select(MaterialModel).where(
                    MaterialModel.source_id == source_id,
                    MaterialModel.external_id == item.external_id,
                )
            )
            if row is None:
                return
            content_changed = row.content_hash != item.content_hash
            values = {
                "title": item.title,
                "url": item.url,
                "canonical_url": item.canonical_url,
                "published_at": item.published_at,
                "collected_at": item.collected_at,
                "description": item.description,
                "author": item.author,
                "content_hash": item.content_hash,
                "normalized_title": item.normalized_title,
            }
            for key, value in values.items():
                if getattr(row, key) != value:
                    setattr(row, key, value)
            if content_changed:
                row.categories = [category.value for category in item.categories]
                if row.delivered_at is None:
                    # A cached editorial card describes the previous content. Do
                    # not let its retry state carry over to a changed material.
                    row.llm_enrichment = None
                    row.llm_model = None
                    row.llm_usage = None
                    row.editorial_attempts = 0
                    row.editorial_retry_at = None
                    row.editorial_error = None
                    row.editorial_failed_at = None
            await self._record_metrics(session, row, item.popularity, captured_at)
            await session.commit()

    async def _record_metrics(
        self,
        session: Any,
        row: MaterialModel,
        metrics: dict[str, float],
        captured_at: datetime,
    ) -> None:
        if not metrics:
            return
        row.raw_metrics = dict(metrics)
        row.last_signal_at = captured_at
        session.add(
            MetricSnapshotModel(material_id=row.id, captured_at=captured_at, metrics=dict(metrics))
        )
        await self._aggregate_root_metrics(session, row.duplicate_of_id or row.id, captured_at)

    async def _aggregate_root_metrics(
        self, session: Any, root_id: UUID, captured_at: datetime
    ) -> None:
        root = await session.get(MaterialModel, root_id)
        if root is None:
            return
        rows = (
            await session.scalars(
                select(MaterialModel).where(
                    or_(MaterialModel.id == root_id, MaterialModel.duplicate_of_id == root_id)
                )
            )
        ).all()
        aggregate = _aggregate_metrics([row.raw_metrics for row in rows])
        previous = await session.scalar(
            select(MetricSnapshotModel)
            .where(MetricSnapshotModel.material_id == root_id)
            .order_by(MetricSnapshotModel.captured_at.desc())
            .limit(1)
        )
        root.popularity = _with_growth(aggregate, previous, captured_at)
        root.last_signal_at = captured_at
        session.add(
            MetricSnapshotModel(material_id=root_id, captured_at=captured_at, metrics=aggregate)
        )

    async def metric_history(self, material_id: UUID) -> list[MetricSnapshot]:
        async with self.database.session() as session:
            rows = (
                await session.scalars(
                    select(MetricSnapshotModel)
                    .where(MetricSnapshotModel.material_id == material_id)
                    .order_by(MetricSnapshotModel.captured_at)
                )
            ).all()
            return [
                MetricSnapshot(captured_at=row.captured_at, metrics=row.metrics) for row in rows
            ]

    async def metric_histories(self, material_ids: list[UUID]) -> dict[UUID, list[MetricSnapshot]]:
        if not material_ids:
            return {}
        async with self.database.session() as session:
            rows = (
                await session.scalars(
                    select(MetricSnapshotModel)
                    .where(MetricSnapshotModel.material_id.in_(material_ids))
                    .order_by(MetricSnapshotModel.captured_at)
                )
            ).all()
        result: dict[UUID, list[MetricSnapshot]] = {material_id: [] for material_id in material_ids}
        for row in rows:
            result[row.material_id].append(
                MetricSnapshot(captured_at=row.captured_at, metrics=row.metrics)
            )
        return result

    async def save_ranking(self, ranked: RankedMaterial) -> None:
        await self.save_rankings([ranked])

    async def save_rankings(self, ranked_materials: list[RankedMaterial]) -> None:
        if not ranked_materials:
            return
        async with self.database.session() as session:
            for ranked in ranked_materials:
                await session.execute(
                    update(MaterialModel)
                    .where(MaterialModel.id == ranked.material_id)
                    .values(score=ranked.score, score_reasons=ranked.score_reasons)
                )
            await session.commit()

    async def save_discovery_scores(self, values: dict[UUID, float]) -> None:
        if not values:
            return
        async with self.database.session() as session:
            for material_id, score in values.items():
                await session.execute(
                    update(MaterialModel)
                    .where(MaterialModel.id == material_id)
                    .values(discovery_score=score)
                )
            await session.commit()

    async def record_selection(self, material_ids: list[UUID]) -> None:
        if not material_ids:
            return
        async with self.database.session() as session:
            await session.execute(
                update(MaterialModel)
                .where(MaterialModel.id.in_(material_ids))
                .values(selected_at=datetime.now(UTC))
            )
            await session.commit()

    async def digest_candidates(
        self, *, lookback_hours: int, delivery_claim_lease_seconds: int = 300
    ) -> list[StoredMaterial]:
        now = datetime.now(UTC)
        since = now - timedelta(hours=lookback_hours)
        stale_claim_before = now - timedelta(seconds=delivery_claim_lease_seconds)
        async with self.database.session() as session:
            fresh_snapshot = exists(
                select(MetricSnapshotModel.id).where(
                    MetricSnapshotModel.material_id == MaterialModel.id,
                    MetricSnapshotModel.captured_at >= since,
                )
            )
            query = (
                select(MaterialModel)
                .where(
                    or_(
                        MaterialModel.collected_at >= since,
                        MaterialModel.last_signal_at >= since,
                        fresh_snapshot,
                        # A paid card remains eligible until delivery reaches a
                        # terminal state. A card with a permanent render failure is
                        # deliberately excluded below.
                        MaterialModel.llm_enrichment.is_not(None),
                        # Failed editorial work is retried only at its persisted,
                        # bounded retry time; old ordinary materials cannot backlog.
                        MaterialModel.editorial_retry_at <= now,
                    ),
                    MaterialModel.duplicate_of_id.is_(None),
                    MaterialModel.delivered_at.is_(None),
                    # A claim is only a lease before a Telegram outcome is known.
                    # Ambiguous outcomes are deliberately held for manual recovery.
                    or_(
                        MaterialModel.delivery_started_at.is_(None),
                        MaterialModel.delivery_started_at <= stale_claim_before,
                    ),
                    MaterialModel.delivery_ambiguous_at.is_(None),
                    MaterialModel.editorial_failed_at.is_(None),
                    or_(
                        MaterialModel.editorial_retry_at.is_(None),
                        MaterialModel.editorial_retry_at <= now,
                    ),
                    or_(
                        MaterialModel.delivery_retry_at.is_(None),
                        MaterialModel.delivery_retry_at <= now,
                    ),
                )
                .options(selectinload(MaterialModel.source))
                .order_by(MaterialModel.published_at.desc())
            )
            rows = (await session.scalars(query)).all()
            return [self._to_stored(row) for row in rows]

    async def save_enrichment(
        self,
        material_id: UUID,
        enrichment: EditorialEnrichment,
        *,
        model: str,
        usage: dict[str, int],
    ) -> None:
        async with self.database.session() as session:
            await session.execute(
                update(MaterialModel)
                .where(MaterialModel.id == material_id)
                .values(
                    llm_enrichment=enrichment.model_dump(mode="json"),
                    llm_model=model,
                    llm_usage=usage,
                    editorial_error=None,
                    editorial_retry_at=None,
                    editorial_failed_at=None,
                )
            )
            await session.commit()

    async def record_editorial_failure(
        self,
        material_id: UUID,
        *,
        error: str,
        retry_delay_seconds: int | None,
    ) -> None:
        """Persist one failed editorial attempt, terminally once retries are exhausted."""

        now = datetime.now(UTC)
        async with self.database.session() as session:
            row = await session.get(MaterialModel, material_id)
            if row is None:
                return
            attempts = row.editorial_attempts + 1
            row.editorial_attempts = attempts
            row.editorial_error = error[:1000]
            if retry_delay_seconds is None:
                row.editorial_retry_at = None
                row.editorial_failed_at = now
            else:
                row.editorial_retry_at = now + timedelta(seconds=retry_delay_seconds)
            await session.commit()

    async def begin_delivery(self, material_id: UUID, *, lease_seconds: int = 300) -> bool:
        """Durably claim a card before Telegram receives it.

        A stale pre-request claim can be taken over after its lease. A separately
        recorded ambiguous Telegram outcome is never reclaimed automatically.
        """

        now = datetime.now(UTC)
        stale_claim_before = now - timedelta(seconds=lease_seconds)
        async with self.database.session() as session:
            claimed_id = await session.scalar(
                update(MaterialModel)
                .where(
                    MaterialModel.id == material_id,
                    MaterialModel.delivered_at.is_(None),
                    MaterialModel.delivery_ambiguous_at.is_(None),
                    or_(
                        MaterialModel.delivery_started_at.is_(None),
                        MaterialModel.delivery_started_at <= stale_claim_before,
                    ),
                )
                .values(delivery_started_at=now, delivery_error=None)
                .returning(MaterialModel.id)
            )
            await session.commit()
            return claimed_id is not None

    async def mark_delivery_ambiguous(self, material_id: UUID, *, error: str) -> None:
        """Hold a possibly-sent card until the owner explicitly recovers it."""

        now = datetime.now(UTC)
        async with self.database.session() as session:
            await session.execute(
                update(MaterialModel)
                .where(MaterialModel.id == material_id, MaterialModel.delivered_at.is_(None))
                .values(
                    delivery_ambiguous_at=now,
                    delivery_error=error[:1000],
                    delivery_retry_at=None,
                )
            )
            await session.commit()

    async def recover_ambiguous_delivery(self, material_id: UUID) -> bool:
        """Explicit owner action to make a held ambiguous card eligible again."""

        async with self.database.session() as session:
            recovered_id = await session.scalar(
                update(MaterialModel)
                .where(
                    MaterialModel.id == material_id,
                    MaterialModel.delivered_at.is_(None),
                    MaterialModel.delivery_ambiguous_at.is_not(None),
                )
                .values(
                    delivery_started_at=None,
                    delivery_ambiguous_at=None,
                    delivery_retry_at=None,
                    delivery_error="manual recovery requested",
                )
                .returning(MaterialModel.id)
            )
            await session.commit()
            return recovered_id is not None

    async def release_delivery_for_retry(
        self, material_id: UUID, *, error: str, retry_after_seconds: int
    ) -> None:
        async with self.database.session() as session:
            await session.execute(
                update(MaterialModel)
                .where(MaterialModel.id == material_id, MaterialModel.delivered_at.is_(None))
                .values(
                    delivery_started_at=None,
                    delivery_ambiguous_at=None,
                    delivery_retry_at=datetime.now(UTC) + timedelta(seconds=retry_after_seconds),
                    delivery_error=error[:1000],
                )
            )
            await session.commit()

    async def create_digest_run(self, *, dry_run: bool) -> UUID:
        async with self.database.session() as session:
            row = DigestRunModel(dry_run=dry_run)
            session.add(row)
            await session.commit()
            return row.id

    async def finish_digest_run(
        self,
        run_id: UUID,
        *,
        status: str,
        collected_count: int = 0,
        inserted_count: int = 0,
        duplicate_count: int = 0,
        candidate_count: int = 0,
        selected_count: int = 0,
        delivered_count: int = 0,
        editorial_failure_count: int = 0,
        error: str | None = None,
    ) -> None:
        async with self.database.session() as session:
            await session.execute(
                update(DigestRunModel)
                .where(DigestRunModel.id == run_id)
                .values(
                    status=status,
                    finished_at=datetime.now(UTC),
                    collected_count=collected_count,
                    inserted_count=inserted_count,
                    duplicate_count=duplicate_count,
                    candidate_count=candidate_count,
                    selected_count=selected_count,
                    delivered_count=delivered_count,
                    editorial_failure_count=editorial_failure_count,
                    error=error,
                )
            )
            await session.commit()

    async def record_deliveries(
        self,
        run_id: UUID,
        receipts: list[tuple[UUID, str | None]],
    ) -> None:
        if not receipts:
            return
        sent_at = datetime.now(UTC)
        async with self.database.session() as session:
            for material_id, message_id in receipts:
                session.add(
                    DeliveryModel(
                        digest_run_id=run_id,
                        material_id=material_id,
                        telegram_message_id=message_id,
                        sent_at=sent_at,
                    )
                )
                await session.execute(
                    update(MaterialModel)
                    .where(MaterialModel.id == material_id)
                    .values(
                        delivered_at=sent_at,
                        delivery_started_at=None,
                        delivery_ambiguous_at=None,
                        delivery_retry_at=None,
                        delivery_error=None,
                    )
                )
            await session.commit()

    async def record_feedback(
        self,
        *,
        material_id: UUID,
        feedback_type: FeedbackType,
        telegram_update_id: int | None = None,
    ) -> MaterialFeedback | None:
        """Store one owner's latest verdict for a material, atomically.

        A Telegram update ID makes polling idempotent. The unique material constraint
        deliberately turns repeated button presses into an update rather than history.
        """

        now = datetime.now(UTC)
        async with self.database.session() as session:
            if telegram_update_id is not None:
                if await session.get(TelegramUpdateModel, telegram_update_id) is not None:
                    return None
                session.add(TelegramUpdateModel(update_id=telegram_update_id))

            material = await session.scalar(
                select(MaterialModel)
                .where(MaterialModel.id == material_id)
                .options(selectinload(MaterialModel.source))
            )
            if material is None:
                await session.rollback()
                return None

            feedback = await session.scalar(
                select(FeedbackModel).where(FeedbackModel.material_id == material_id)
            )
            values = {
                "feedback_type": feedback_type.value,
                "source_key": material.source.key,
                "categories": list(material.categories),
                "importance_score": material.score,
                "discovery_score": material.discovery_score,
                "updated_at": now,
            }
            if feedback is None:
                feedback = FeedbackModel(material_id=material_id, **values)
                session.add(feedback)
            else:
                for key, value in values.items():
                    setattr(feedback, key, value)
            await session.flush()
            result = MaterialFeedback(
                material_id=feedback.material_id,
                feedback_type=FeedbackType(feedback.feedback_type),
                updated_at=feedback.updated_at,
                source_key=feedback.source_key,
                categories=[Category(value) for value in feedback.categories],
                importance_score=feedback.importance_score,
                discovery_score=feedback.discovery_score,
            )
            await session.commit()
            return result

    async def latest_telegram_update_id(self) -> int | None:
        async with self.database.session() as session:
            value = await session.scalar(select(func.max(TelegramUpdateModel.update_id)))
            return int(value) if value is not None else None

    async def mark_telegram_update_processed(self, update_id: int) -> None:
        async with self.database.session() as session:
            if await session.get(TelegramUpdateModel, update_id) is None:
                session.add(TelegramUpdateModel(update_id=update_id))
                await session.commit()

    async def report_materials(self, *, days: int) -> list[MaterialModel]:
        since = datetime.now(UTC) - timedelta(days=days)
        async with self.database.session() as session:
            rows = (
                await session.scalars(
                    select(MaterialModel)
                    .where(MaterialModel.collected_at >= since)
                    .options(selectinload(MaterialModel.source))
                )
            ).all()
            return list(rows)

    async def report_feedback(self, *, days: int) -> list[FeedbackModel]:
        since = datetime.now(UTC) - timedelta(days=days)
        async with self.database.session() as session:
            rows = (
                await session.scalars(
                    select(FeedbackModel).where(FeedbackModel.updated_at >= since)
                )
            ).all()
            return list(rows)

    async def report_deliveries(self, *, days: int) -> list[DeliveryModel]:
        since = datetime.now(UTC) - timedelta(days=days)
        async with self.database.session() as session:
            rows = (
                await session.scalars(select(DeliveryModel).where(DeliveryModel.sent_at >= since))
            ).all()
            return list(rows)

    async def counts(self) -> dict[str, int]:
        async with self.database.session() as session:
            materials = await session.scalar(select(func.count()).select_from(MaterialModel))
            sources = await session.scalar(select(func.count()).select_from(SourceModel))
            undelivered = await session.scalar(
                select(func.count())
                .select_from(MaterialModel)
                .where(
                    MaterialModel.duplicate_of_id.is_(None),
                    MaterialModel.delivered_at.is_(None),
                )
            )
            return {
                "sources": int(sources or 0),
                "materials": int(materials or 0),
                "undelivered": int(undelivered or 0),
            }

    @staticmethod
    def _to_stored(row: MaterialModel) -> StoredMaterial:
        item = NormalizedItem(
            external_id=row.external_id,
            source_key=row.source.key,
            source_name=row.source.name,
            source_reputation=row.source.reputation,
            title=row.title,
            url=row.url,
            canonical_url=row.canonical_url,
            published_at=row.published_at,
            collected_at=row.collected_at,
            description=row.description,
            author=row.author,
            source_categories=[Category(value) for value in row.source.default_categories],
            categories=[Category(value) for value in row.categories],
            popularity=row.popularity,
            content_hash=row.content_hash,
            normalized_title=row.normalized_title,
        )
        return StoredMaterial(
            id=row.id,
            item=item,
            duplicate_of_id=row.duplicate_of_id,
            independent_mentions=row.independent_mentions,
            score=row.score,
            score_reasons=row.score_reasons,
            discovery_score=row.discovery_score,
            delivered_at=row.delivered_at,
            llm_enrichment=(
                EditorialEnrichment.model_validate(row.llm_enrichment)
                if row.llm_enrichment is not None
                else None
            ),
            llm_model=row.llm_model,
            llm_usage=row.llm_usage or {},
            editorial_attempts=row.editorial_attempts,
            editorial_retry_at=row.editorial_retry_at,
            delivery_started_at=row.delivery_started_at,
            delivery_ambiguous_at=row.delivery_ambiguous_at,
        )


_GROWTH_METRIC_KEYS = (
    "github_stars",
    "hn_comments",
    "hn_points",
    "reddit_comments",
    "reddit_upvotes",
    "youtube_likes",
    "youtube_views",
    "points",
    "views",
    "stars",
    "upvotes",
)
_DERIVED_METRIC_SUFFIXES = ("_per_hour",)
_DERIVED_METRIC_KEYS = frozenset(
    {"growth_absolute", "growth_percent", "growth_per_hour", "growth_window_hours"}
)


def _provider_family(source_key: str) -> str:
    return "reddit" if source_key.startswith("reddit-") else source_key


def _aggregate_metrics(metric_sets: list[dict[str, float]]) -> dict[str, float]:
    """Keep the strongest observation per provider metric without crosspost sums."""

    aggregate: dict[str, float] = {}
    for metrics in metric_sets:
        for key, value in metrics.items():
            if key in _DERIVED_METRIC_KEYS or key.endswith(_DERIVED_METRIC_SUFFIXES):
                continue
            numeric = float(value)
            aggregate[key] = max(aggregate.get(key, numeric), numeric)
    return aggregate


def _with_growth(
    metrics: dict[str, float], previous: MetricSnapshotModel | None, captured_at: datetime
) -> dict[str, float]:
    result = dict(metrics)
    if previous is None:
        return result
    elapsed_hours = (captured_at - previous.captured_at).total_seconds() / 3600.0
    if elapsed_hours <= 0:
        return result
    best_rate = 0.0
    best_absolute = 0.0
    best_percent = 0.0
    for key in _GROWTH_METRIC_KEYS:
        if key not in metrics or key not in previous.metrics:
            continue
        absolute = float(metrics[key]) - float(previous.metrics[key])
        if absolute < 0:
            continue
        baseline = float(previous.metrics[key])
        percent = (absolute / baseline * 100.0) if baseline > 0 else 0.0
        rate = absolute / elapsed_hours
        result.update(
            {
                f"{key}_absolute": round(absolute, 2),
                f"{key}_percent": round(percent, 2),
                f"{key}_per_hour": round(rate, 2),
            }
        )
        if rate > best_rate:
            best_rate = rate
            best_absolute = absolute
            best_percent = percent
    if best_rate > 0:
        result.update(
            {
                "growth_absolute": round(best_absolute, 2),
                "growth_percent": round(best_percent, 2),
                "growth_per_hour": round(best_rate, 2),
                "growth_window_hours": round(elapsed_hours, 2),
            }
        )
    return result
