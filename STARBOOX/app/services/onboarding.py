"""Soft onboarding for the mandatory-subscription cascade.

Walling a brand-new user behind every sponsor of every provider is the worst
possible first impression: they have to grind through the whole cascade before
they ever see what the bot does, and once they are through, the same resources
they just subscribed to are worthless as tasks.

So the first ``/start`` shows only ``op_intro_sponsors`` sponsors and then lets the
user in for ``op_intro_grace_sec``. During that window they can explore the bot and
earn; afterwards the full cascade applies as usual. Nothing is lost for the
publisher — the sponsors skipped during onboarding are still monetised, just as
*paid* partner tasks the user takes voluntarily.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.db.models import User


def intro_active(user: User, settings: Settings) -> bool:
    """Whether the user is inside the soft-onboarding grace window."""
    if not settings.op_intro_enabled or settings.op_intro_grace_sec <= 0:
        return False
    started = user.op_intro_at
    if started is None:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return datetime.now(UTC) - started < timedelta(seconds=settings.op_intro_grace_sec)


def intro_limit(user: User, settings: Settings) -> int:
    """How many sponsors to show this user, or 0 for «all of them»."""
    if not settings.op_intro_enabled or settings.op_intro_sponsors <= 0:
        return 0
    # The trimmed block is only for people who have not finished onboarding yet.
    if user.op_intro_at is not None and not intro_active(user, settings):
        return 0
    return settings.op_intro_sponsors


def begin_intro(user: User, settings: Settings) -> bool:
    """Open the grace window on the first blocked /start. True if it just opened."""
    if not settings.op_intro_enabled or settings.op_intro_grace_sec <= 0:
        return False
    if user.op_intro_at is not None:
        return False
    user.op_intro_at = datetime.now(UTC)
    return True
