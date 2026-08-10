"""personal editor verdict lifecycle and local translation cache

Revision ID: 0009_personal_editor
Revises: 0008_qualitative_signals
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_personal_editor"
down_revision: str | None = "0008_qualitative_signals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("materials", sa.Column("editorial_rejected_at", sa.DateTime(timezone=True)))
    op.create_table(
        "translation_cache",
        sa.Column("cache_key", sa.String(length=64), primary_key=True),
        sa.Column("source_language", sa.String(length=12), nullable=False),
        sa.Column("target_language", sa.String(length=12), nullable=False),
        sa.Column("source_text_hash", sa.String(length=64), nullable=False),
        sa.Column("translated_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("translation_cache")
    op.drop_column("materials", "editorial_rejected_at")
