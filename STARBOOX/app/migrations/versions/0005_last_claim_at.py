"""Separate the anti-spam cooldown clock from the generic "last seen" timestamp.

``users.last_action_at`` is refreshed by the user middleware on *every* update, so
measuring the claim cooldown against it rejected every reward forever whenever
``CLAIM_COOLDOWN_SECONDS`` was above zero. Rewarded actions now stamp their own
column, ``users.last_claim_at``.

Existing rows are backfilled with ``last_action_at`` so nobody gets a free claim
right after the upgrade; the value ages out within seconds anyway.

Revision ID: 0005_last_claim_at
Revises: 0004_partner_campaigns
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_last_claim_at"
down_revision: str | None = "0004_partner_campaigns"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_claim_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE users SET last_claim_at = last_action_at WHERE last_action_at IS NOT NULL")


def downgrade() -> None:
    op.drop_column("users", "last_claim_at")
