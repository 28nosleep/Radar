"""add release-fix delivery and raw metric state

Revision ID: 0006_release_fixes
Revises: 0005_reliability_batch_a
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_release_fixes"
down_revision: str | None = "0005_reliability_batch_a"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Existing popularity may already be an aggregate. Start legacy rows empty
    # so a historical aggregate can never become a new metric owner.
    op.add_column(
        "materials",
        sa.Column("raw_metrics", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )
    op.alter_column("materials", "raw_metrics", server_default=None)
    op.add_column("materials", sa.Column("delivery_ambiguous_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("materials", "delivery_ambiguous_at")
    op.drop_column("materials", "raw_metrics")
