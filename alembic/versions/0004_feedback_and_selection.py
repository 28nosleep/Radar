"""add feedback and selected material tracking

Revision ID: 0004_feedback_and_selection
Revises: 0003_discovery_score
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_feedback_and_selection"
down_revision: str | None = "0003_discovery_score"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("materials", sa.Column("selected_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "material_feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("feedback_type", sa.String(length=16), nullable=False),
        sa.Column("source_key", sa.String(length=120), nullable=False),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("importance_score", sa.Float(), nullable=False),
        sa.Column("discovery_score", sa.Float(), nullable=False),
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
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("material_id", name="uq_feedback_material"),
    )
    op.create_table(
        "telegram_updates",
        sa.Column("update_id", sa.Integer(), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("update_id"),
    )


def downgrade() -> None:
    op.drop_table("telegram_updates")
    op.drop_table("material_feedback")
    op.drop_column("materials", "selected_at")
