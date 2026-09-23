"""Partner (API) rewarded tasks and paid promotion campaigns.

Revision ID: 0004_partner_campaigns
Revises: 0003_device_checks
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_partner_campaigns"
down_revision: str | None = "0003_device_checks"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "partner_task_claims",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="channel"),
        sa.Column("title", sa.String(160), nullable=False, server_default=""),
        sa.Column("url", sa.String(512), nullable=False, server_default=""),
        sa.Column("reward", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "provider", "external_id", name="uq_partner_claim"),
    )
    op.create_index("ix_partner_task_claims_user_id", "partner_task_claims", ["user_id"])
    op.create_index(
        "ix_partner_claims_provider_created", "partner_task_claims", ["provider", "created_at"]
    )

    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("url", sa.String(512), nullable=False, server_default=""),
        sa.Column("check_chat", sa.String(64), nullable=True),
        sa.Column("reward", sa.Integer(), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("done_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("xtr_price", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("telegram_charge_id", sa.String(128), nullable=True, unique=True),
        sa.Column("moderation_note", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.BigInteger(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_campaigns_owner_id", "campaigns", ["owner_id"])
    op.create_index("ix_campaigns_status_created", "campaigns", ["status", "created_at"])

    op.create_table(
        "campaign_claims",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reward", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("campaign_id", "user_id", name="uq_campaign_claim"),
    )
    op.create_index("ix_campaign_claims_campaign_id", "campaign_claims", ["campaign_id"])
    op.create_index("ix_campaign_claims_user_id", "campaign_claims", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_campaign_claims_user_id", table_name="campaign_claims")
    op.drop_index("ix_campaign_claims_campaign_id", table_name="campaign_claims")
    op.drop_table("campaign_claims")
    op.drop_index("ix_campaigns_status_created", table_name="campaigns")
    op.drop_index("ix_campaigns_owner_id", table_name="campaigns")
    op.drop_table("campaigns")
    op.drop_index("ix_partner_claims_provider_created", table_name="partner_task_claims")
    op.drop_index("ix_partner_task_claims_user_id", table_name="partner_task_claims")
    op.drop_table("partner_task_claims")
