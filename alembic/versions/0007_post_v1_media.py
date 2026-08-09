"""post-v1 media and Reddit metadata

Revision ID: 0007_post_v1_media
Revises: 0006_release_fixes
Create Date: 2026-08-09
"""

import sqlalchemy as sa

from alembic import op

revision = "0007_post_v1_media"
down_revision = "0006_release_fixes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("materials", sa.Column("subreddit", sa.String(length=160), nullable=True))
    op.add_column(
        "materials",
        sa.Column("media_type", sa.String(length=16), nullable=False, server_default="none"),
    )
    op.add_column("materials", sa.Column("media_url", sa.Text(), nullable=True))
    op.add_column("materials", sa.Column("thumbnail_url", sa.Text(), nullable=True))
    op.add_column("materials", sa.Column("media_source", sa.String(length=120), nullable=True))
    op.alter_column("materials", "media_type", server_default=None)


def downgrade() -> None:
    op.drop_column("materials", "media_source")
    op.drop_column("materials", "thumbnail_url")
    op.drop_column("materials", "media_url")
    op.drop_column("materials", "media_type")
    op.drop_column("materials", "subreddit")
