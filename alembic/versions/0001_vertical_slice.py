"""Create the RSS vertical-slice tables.

Revision ID: 0001_vertical_slice
Revises:
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_vertical_slice"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=250), nullable=False),
        sa.Column("feed_url", sa.Text(), nullable=False),
        sa.Column("site_url", sa.Text(), nullable=True),
        sa.Column("reputation", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("default_categories", sa.JSON(), nullable=False),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_table(
        "digest_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("collected_count", sa.Integer(), nullable=False),
        sa.Column("inserted_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("selected_count", sa.Integer(), nullable=False),
        sa.Column("delivered_count", sa.Integer(), nullable=False),
        sa.Column("editorial_failure_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "materials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("popularity", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("normalized_title", sa.Text(), nullable=False),
        sa.Column("duplicate_of_id", sa.Uuid(), nullable=True),
        sa.Column("independent_mentions", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("score_reasons", sa.JSON(), nullable=False),
        sa.Column("llm_enrichment", sa.JSON(), nullable=True),
        sa.Column("llm_model", sa.String(length=120), nullable=True),
        sa.Column("llm_usage", sa.JSON(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["duplicate_of_id"], ["materials.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "external_id",
            name="uq_material_source_external",
        ),
    )
    op.create_index("ix_materials_canonical_url", "materials", ["canonical_url"])
    op.create_index("ix_materials_collected_at", "materials", ["collected_at"])
    op.create_index("ix_materials_content_hash", "materials", ["content_hash"])
    op.create_index("ix_materials_duplicate_of", "materials", ["duplicate_of_id"])
    op.create_index("ix_materials_published_at", "materials", ["published_at"])
    op.create_table(
        "deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("digest_run_id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("telegram_message_id", sa.String(length=120), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["digest_run_id"],
            ["digest_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "digest_run_id",
            "material_id",
            name="uq_delivery_run_material",
        ),
    )


def downgrade() -> None:
    op.drop_table("deliveries")
    op.drop_index("ix_materials_published_at", table_name="materials")
    op.drop_index("ix_materials_duplicate_of", table_name="materials")
    op.drop_index("ix_materials_content_hash", table_name="materials")
    op.drop_index("ix_materials_collected_at", table_name="materials")
    op.drop_index("ix_materials_canonical_url", table_name="materials")
    op.drop_table("materials")
    op.drop_table("digest_runs")
    op.drop_table("sources")
