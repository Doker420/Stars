"""Internal VIP expiry for the game store.

Revision ID: 0010_game_store
Revises: 0009_game_hub
Create Date: 2026-09-23
"""
from __future__ import annotations
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0010_game_store"
down_revision: str | None = "0009_game_hub"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("vip_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("last_free_case_on", sa.Date(), nullable=True))
    op.add_column(
        "case_openings",
        sa.Column("fulfillment_status", sa.String(16), nullable=False, server_default="credited"),
    )


def downgrade() -> None:
    op.drop_column("case_openings", "fulfillment_status")
    op.drop_column("users", "last_free_case_on")
    op.drop_column("users", "vip_until")
