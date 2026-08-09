from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import selectinload

from f117.domain import (
    Category,
    EditorialEnrichment,
    FeedSource,
    MetricSnapshot,
    NormalizedItem,
    RankedMaterial,
    StoredMaterial,
)
from f117.storage.database import Database
from f117.storage.models import (
    DeliveryModel,
    DigestRunModel,
    MaterialModel,
    MetricSnapshotModel,
    SourceModel,
)


@dataclass(frozen=True, slots=True)
class SourceState:
    id: UUID
    source: FeedSource
    etag: str | None
    last_modified: str | None


class Repository:
    def __init__(self, database: Database) -> None:
        self.database = database

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
                    existing_source = await session.scalar(
                        select(MaterialModel.id).where(
                            MaterialModel.source_id == source_id,
                            or_(
                                MaterialModel.id == duplicate_of_id,
                                MaterialModel.duplicate_of_id == duplicate_of_id,
                            ),
                        )
                    )
                    should_increment_mentions = existing_source is None

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
                    content_hash=item.content_hash,
                    normalized_title=item.normalized_title,
                    duplicate_of_id=duplicate_of_id,
                )
                session.add(row)
                if should_increment_mentions:
                    await session.execute(
                        update(MaterialModel)
                        .where(MaterialModel.id == duplicate_of_id)
                        .values(independent_mentions=MaterialModel.independent_mentions + 1)
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
                stored = self._to_stored(row)
                await session.commit()
                return stored
            except Exception:
                await session.rollback()
                raise

    async def refresh_observation(
        self, source_id: UUID, external_id: str, metrics: dict[str, float]
    ) -> None:
        """Persist an observation and derived growth atomically for an existing item."""

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
            previous = await session.scalar(
                select(MetricSnapshotModel)
                .where(MetricSnapshotModel.material_id == row.id)
                .order_by(MetricSnapshotModel.captured_at.desc())
                .limit(1)
            )
            updated_metrics = _with_growth(metrics, previous, captured_at)
            row.popularity = updated_metrics
            session.add(
                MetricSnapshotModel(
                    material_id=row.id, captured_at=captured_at, metrics=dict(metrics)
                )
            )
            await session.commit()

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

    async def digest_candidates(self, *, lookback_hours: int) -> list[StoredMaterial]:
        since = datetime.now(UTC) - timedelta(hours=lookback_hours)
        async with self.database.session() as session:
            query = (
                select(MaterialModel)
                .where(
                    # Eligibility is based on first sighting, not publisher time:
                    # feeds may surface important older posts after downtime.
                    or_(
                        MaterialModel.collected_at >= since,
                        # Once paid enrichment exists, keep retrying delivery even
                        # after the ordinary candidate window has elapsed.
                        MaterialModel.llm_enrichment.is_not(None),
                    ),
                    MaterialModel.duplicate_of_id.is_(None),
                    MaterialModel.delivered_at.is_(None),
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
                    .values(delivered_at=sent_at)
                )
            await session.commit()

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
        )


_GROWTH_METRIC_KEYS = (
    "github_stars",
    "reddit_upvotes",
    "youtube_views",
    "points",
    "views",
    "stars",
    "upvotes",
)


def _with_growth(
    metrics: dict[str, float], previous: MetricSnapshotModel | None, captured_at: datetime
) -> dict[str, float]:
    result = dict(metrics)
    if previous is None:
        return result
    elapsed_hours = (captured_at - previous.captured_at).total_seconds() / 3600.0
    if elapsed_hours <= 0:
        return result
    for key in _GROWTH_METRIC_KEYS:
        if key not in metrics or key not in previous.metrics:
            continue
        absolute = float(metrics[key]) - float(previous.metrics[key])
        if absolute < 0:
            continue
        baseline = float(previous.metrics[key])
        percent = (absolute / baseline * 100.0) if baseline > 0 else 0.0
        result.update(
            {
                "growth_absolute": round(absolute, 2),
                "growth_percent": round(percent, 2),
                "growth_per_hour": round(percent / elapsed_hours, 2),
                "growth_window_hours": round(elapsed_hours, 2),
            }
        )
        return result
    return result
