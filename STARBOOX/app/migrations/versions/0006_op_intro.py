"""Soft onboarding window for the mandatory-subscription cascade.

``users.op_intro_at`` marks when the user first saw the trimmed sponsor block. While
the window is open they may use the bot; afterwards the full cascade applies.

Existing users are backfilled with their registration date, so the window is long
expired for them and nothing changes.

Revision ID: 0006_op_intro
Revises: 0005_last_claim_at
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_op_intro"
down_revision: str | None = "0005_last_claim_at"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("op_intro_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE users SET op_intro_at = created_at")


def downgrade() -> None:
    op.drop_column("users", "op_intro_at")
