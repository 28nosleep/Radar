"""persist qualitative collection signals

Revision ID: 0008_qualitative_signals
Revises: 0007_post_v1_media
Create Date: 2026-08-10
"""

import sqlalchemy as sa

from alembic import op

revision = "0008_qualitative_signals"
down_revision = "0007_post_v1_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "materials",
        sa.Column("qualitative_signals", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.alter_column("materials", "qualitative_signals", server_default=None)


def downgrade() -> None:
    op.drop_column("materials", "qualitative_signals")
