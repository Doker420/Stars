"""Extended campaign formats and Telegram Premium targeting.

Revision ID: 0008_campaign_formats
Revises: 0007_task_digest
Create Date: 2026-09-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_campaign_formats"
down_revision: str | None = "0007_task_digest"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column("premium_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "campaigns",
        sa.Column("verification_mode", sa.String(length=24), nullable=False, server_default="manual"),
    )
    op.create_index("ix_campaigns_premium_status", "campaigns", ["premium_only", "status"])


def downgrade() -> None:
    op.drop_index("ix_campaigns_premium_status", table_name="campaigns")
    op.drop_column("campaigns", "verification_mode")
    op.drop_column("campaigns", "premium_only")
