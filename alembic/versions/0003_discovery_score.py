"""add discovery score

Revision ID: 0003_discovery_score
Revises: 0002_metric_snapshots
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_discovery_score"
down_revision: str | None = "0002_metric_snapshots"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "materials", sa.Column("discovery_score", sa.Float(), nullable=False, server_default="0")
    )
    op.alter_column("materials", "discovery_score", server_default=None)


def downgrade() -> None:
    op.drop_column("materials", "discovery_score")
