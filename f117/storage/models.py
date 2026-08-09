from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SourceModel(Base):
    __tablename__ = "sources"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(250), nullable=False)
    feed_url: Mapped[str] = mapped_column(Text, nullable=False)
    site_url: Mapped[str | None] = mapped_column(Text)
    reputation: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    default_categories: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    materials: Mapped[list[MaterialModel]] = relationship(back_populates="source")


class MaterialModel(Base):
    __tablename__ = "materials"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_material_source_external"),
        Index("ix_materials_canonical_url", "canonical_url"),
        Index("ix_materials_content_hash", "content_hash"),
        Index("ix_materials_published_at", "published_at"),
        Index("ix_materials_collected_at", "collected_at"),
        Index("ix_materials_duplicate_of", "duplicate_of_id"),
        Index("ix_materials_editorial_retry_at", "editorial_retry_at"),
        Index("ix_materials_last_signal_at", "last_signal_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author: Mapped[str | None] = mapped_column(Text)
    categories: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    popularity: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_title: Mapped[str] = mapped_column(Text, nullable=False)

    duplicate_of_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("materials.id", ondelete="SET NULL")
    )
    independent_mentions: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    discovery_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    llm_enrichment: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    llm_model: Mapped[str | None] = mapped_column(String(120))
    llm_usage: Mapped[dict[str, int] | None] = mapped_column(JSON)
    editorial_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    editorial_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    editorial_error: Mapped[str | None] = mapped_column(Text)
    editorial_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_error: Mapped[str | None] = mapped_column(Text)
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_signal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source: Mapped[SourceModel] = relationship(back_populates="materials")
    duplicate_of: Mapped[MaterialModel | None] = relationship(
        remote_side="MaterialModel.id", foreign_keys=[duplicate_of_id]
    )


class DigestRunModel(Base):
    __tablename__ = "digest_runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    dry_run: Mapped[bool] = mapped_column(nullable=False, default=False)
    collected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    editorial_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class DeliveryModel(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("digest_run_id", "material_id", name="uq_delivery_run_material"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    digest_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("digest_runs.id", ondelete="CASCADE"), nullable=False
    )
    material_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("materials.id", ondelete="CASCADE"), nullable=False
    )
    telegram_message_id: Mapped[str | None] = mapped_column(String(120))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MetricSnapshotModel(Base):
    __tablename__ = "metric_snapshots"
    __table_args__ = (Index("ix_metric_snapshots_material_captured", "material_id", "captured_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("materials.id", ondelete="CASCADE"), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metrics: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False, default=dict)


class FeedbackModel(Base):
    __tablename__ = "material_feedback"
    __table_args__ = (UniqueConstraint("material_id", name="uq_feedback_material"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("materials.id", ondelete="CASCADE"), nullable=False
    )
    feedback_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_key: Mapped[str] = mapped_column(String(120), nullable=False)
    categories: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    importance_score: Mapped[float] = mapped_column(Float, nullable=False)
    discovery_score: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TelegramUpdateModel(Base):
    __tablename__ = "telegram_updates"

    update_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
