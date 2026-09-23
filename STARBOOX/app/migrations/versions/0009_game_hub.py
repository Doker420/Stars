"""Case keys, wheel spins and idempotent game history.

Revision ID: 0009_game_hub
Revises: 0008_campaign_formats
Create Date: 2026-09-23
"""
from __future__ import annotations
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0009_game_hub"
down_revision: str | None = "0008_campaign_formats"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("case_keys", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("users", sa.Column("spins", sa.Integer(), nullable=False, server_default="1"))
    op.create_table(
        "case_openings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("case_slug", sa.String(32), nullable=False),
        sa.Column("payment_method", sa.String(16), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("reward_kind", sa.String(16), nullable=False),
        sa.Column("reward_amount", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_case_openings_user_created", "case_openings", ["user_id", "created_at"])
    op.create_table(
        "wheel_spins",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reward_kind", sa.String(16), nullable=False),
        sa.Column("reward_amount", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_wheel_spins_user_created", "wheel_spins", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("wheel_spins")
    op.drop_table("case_openings")
    op.drop_column("users", "spins")
    op.drop_column("users", "case_keys")
