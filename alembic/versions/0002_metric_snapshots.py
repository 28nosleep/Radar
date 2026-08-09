"""add metric snapshots

Revision ID: 0002_metric_snapshots
Revises: 0001_vertical_slice
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_metric_snapshots"
down_revision: str | None = "0001_vertical_slice"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "metric_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_metric_snapshots_material_captured",
        "metric_snapshots",
        ["material_id", "captured_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_metric_snapshots_material_captured", table_name="metric_snapshots")
    op.drop_table("metric_snapshots")
