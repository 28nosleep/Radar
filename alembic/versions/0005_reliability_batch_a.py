"""add reliability state for editorial and Telegram delivery

Revision ID: 0005_reliability_batch_a
Revises: 0004_feedback_and_selection
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_reliability_batch_a"
down_revision: str | None = "0004_feedback_and_selection"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "materials",
        sa.Column("editorial_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("materials", sa.Column("editorial_retry_at", sa.DateTime(timezone=True)))
    op.add_column("materials", sa.Column("editorial_error", sa.Text()))
    op.add_column("materials", sa.Column("editorial_failed_at", sa.DateTime(timezone=True)))
    op.add_column("materials", sa.Column("delivery_started_at", sa.DateTime(timezone=True)))
    op.add_column("materials", sa.Column("delivery_retry_at", sa.DateTime(timezone=True)))
    op.add_column("materials", sa.Column("delivery_error", sa.Text()))
    op.add_column("materials", sa.Column("last_signal_at", sa.DateTime(timezone=True)))
    op.create_index("ix_materials_editorial_retry_at", "materials", ["editorial_retry_at"])
    op.create_index("ix_materials_last_signal_at", "materials", ["last_signal_at"])


def downgrade() -> None:
    op.drop_index("ix_materials_last_signal_at", table_name="materials")
    op.drop_index("ix_materials_editorial_retry_at", table_name="materials")
    op.drop_column("materials", "last_signal_at")
    op.drop_column("materials", "delivery_error")
    op.drop_column("materials", "delivery_retry_at")
    op.drop_column("materials", "delivery_started_at")
    op.drop_column("materials", "editorial_failed_at")
    op.drop_column("materials", "editorial_error")
    op.drop_column("materials", "editorial_retry_at")
    op.drop_column("materials", "editorial_attempts")
