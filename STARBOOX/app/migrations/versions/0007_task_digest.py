"""Periodic task reminders: per-user opt-out and last-sent stamp.

``users.task_digest_enabled`` is the user-facing toggle; ``users.last_task_digest_at``
keeps the interval honest across restarts, so redeploying cannot replay a wave of
notifications to everyone.

Existing users are opted in (that is the product default) but stamped with the
current time, so nobody is messaged the moment this migration lands — the first
reminder arrives one full interval later.

Revision ID: 0007_task_digest
Revises: 0006_op_intro
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_task_digest"
down_revision: str | None = "0006_op_intro"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "task_digest_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "users",
        sa.Column("last_task_digest_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Quiet start: no burst of reminders right after the upgrade.
    op.execute("UPDATE users SET last_task_digest_at = CURRENT_TIMESTAMP")


def downgrade() -> None:
    op.drop_column("users", "last_task_digest_at")
    op.drop_column("users", "task_digest_enabled")
