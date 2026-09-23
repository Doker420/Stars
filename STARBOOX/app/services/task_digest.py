"""Periodic "tasks are waiting for you" reminder.

The bot already has plenty of work to offer (built-in tasks, partner offers from
the monetization APIs, paid user campaigns), but a user only discovers it by
walking into the «Задания» screen. This module pushes a short digest instead, so
inventory that arrives after the user's last visit does not sit unseen.

Design constraints that shaped it:

* **Never spam.** A user is written to at most once per
  ``task_digest_interval_hours``, and the timestamp lives in the database
  (``users.last_task_digest_at``) — restarting the bot cannot replay a wave.
* **Never at night.** Quiet hours are honoured; the worker simply waits.
* **Opt-out is real.** ``users.task_digest_enabled`` is checked in the SQL query,
  not after rendering, so disabled users cost nothing.
* **Nothing to say → say nothing.** The digest is skipped unless the user has at
  least ``task_digest_min_tasks`` things they could actually do right now.
* **Best effort.** Telegram errors are swallowed per user; a blocked bot marks the
  user so the audience shrinks by itself.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from aiogram import Bot
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramNotFound,
    TelegramRetryAfter,
)
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Task, User, UserTask
from app.db.txn import commit_before_io
from app.op.gate import OpGate
from app.services import campaigns as campaign_service
from app.services import partner_tasks as partner_service

log = structlog.get_logger("kodostars.digest")

# (user_id, text) → send one reminder. Injected so tests never touch Telegram.
DigestSender = Callable[[int, "DigestPayload"], Awaitable[None]]


@dataclass(slots=True)
class DigestPayload:
    """What a single user is being reminded about."""

    tasks: int = 0
    campaigns: int = 0
    partner: int = 0

    @property
    def total(self) -> int:
        return self.tasks + self.campaigns + self.partner


def quiet_now(settings: Settings, *, now: datetime | None = None) -> bool:
    """Whether the current hour falls inside the do-not-disturb window."""
    start = settings.task_digest_quiet_from
    end = settings.task_digest_quiet_to
    if start == end:
        return False
    hour = (now or datetime.now(UTC)).hour
    if start < end:
        return start <= hour < end
    # Window wraps midnight (e.g. 23 → 9).
    return hour >= start or hour < end


def _due_before(settings: Settings, *, now: datetime | None = None) -> datetime:
    interval = max(settings.task_digest_interval_hours, 1)
    return (now or datetime.now(UTC)) - timedelta(hours=interval)


async def due_users(
    session: AsyncSession,
    settings: Settings,
    *,
    limit: int = 500,
    now: datetime | None = None,
) -> list[User]:
    """Users eligible for a reminder right now, oldest reminder first.

    Excludes banned users, users who blocked the bot, users who opted out and
    anyone reminded within the interval. Users who never finished ``/start`` are
    skipped too — they have not seen the bot yet.
    """
    cutoff = _due_before(settings, now=now)
    stmt = (
        select(User)
        .where(
            User.is_banned.is_(False),
            User.blocked_bot_at.is_(None),
            User.task_digest_enabled.is_(True),
            User.started_at.is_not(None),
            or_(User.last_task_digest_at.is_(None), User.last_task_digest_at <= cutoff),
        )
        .order_by(User.last_task_digest_at.is_not(None), User.last_task_digest_at, User.id)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def build_payload(
    session: AsyncSession,
    *,
    user: User,
    settings: Settings,
    gate: OpGate | None = None,
    bot: Bot | None = None,
) -> DigestPayload:
    """Count what this user could do right now.

    Partner offers are counted first and are the reason this digest exists: they
    are the monetized inventory, so a reminder that ignored them would push users
    toward the work that earns the project nothing.

    Counting them costs a round trip to the provider APIs per user, which is why
    it is gated behind ``task_digest_include_partner`` and the batch size — see
    :class:`TaskDigestRunner`. If the gate is unavailable or every provider fails,
    the count is simply 0 and the local sources still carry the reminder.
    """
    done = select(UserTask.task_id).where(UserTask.user_id == user.id)
    open_tasks = await session.execute(
        select(Task.id).where(Task.is_active.is_(True), Task.id.not_in(done))
    )
    campaigns = await campaign_service.list_available(session, user=user, settings=settings, limit=20)

    partner = 0
    if gate is not None and settings.partner_tasks_enabled and settings.task_digest_include_partner:
        try:
            offers = await partner_service.list_offers(
                session, user=user, gate=gate, settings=settings, bot=bot
            )
            partner = len(offers)
        except Exception:
            # Never let a provider outage stop the reminder.
            log.info("task_digest_partner_failed", user_id=user.id, exc_info=True)

    return DigestPayload(
        tasks=len(list(open_tasks.scalars().all())),
        campaigns=len(campaigns),
        partner=partner,
    )


def mark_sent(user: User, *, now: datetime | None = None) -> None:
    user.last_task_digest_at = now or datetime.now(UTC)


async def set_opt_in(session: AsyncSession, *, user: User, enabled: bool) -> User:
    """User-facing toggle for the reminders."""
    user.task_digest_enabled = enabled
    await session.flush()
    return user


class TaskDigestRunner:
    """Background worker that sends the reminders.

    One pass per ``tick_seconds``: pick due users, count their work, send. The loop
    owns its own sessions (it runs outside any request) and commits before each
    network call so the SQLite write lock is never held across I/O.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        sender: DigestSender,
        settings: Settings,
        settings_provider: Callable[[AsyncSession], Awaitable[Settings]] | None = None,
        gate: OpGate | None = None,
        bot: Bot | None = None,
        tick_seconds: float = 300.0,
        batch_size: int = 200,
    ) -> None:
        self._factory = session_factory
        self._sender = sender
        self._settings = settings
        self._settings_provider = settings_provider
        self._gate = gate
        self._bot = bot
        self._tick = max(tick_seconds, 1.0)
        self._batch = max(batch_size, 1)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def _effective(self, session: AsyncSession) -> Settings:
        if self._settings_provider is None:
            return self._settings
        return await self._settings_provider(session)

    def start(self) -> asyncio.Task[None]:
        if self._task is not None and not self._task.done():
            return self._task
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="task-digest")
        return self._task

    async def shutdown(self) -> None:
        self._stop.set()
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        # Shutdown must never raise, whatever the worker was doing.
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _loop(self) -> None:
        log.info("task_digest_started", tick=self._tick)
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("task_digest_pass_failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick)
            except TimeoutError:
                continue
        log.info("task_digest_stopped")

    async def run_once(self, *, now: datetime | None = None) -> int:
        """One pass. Returns how many reminders were delivered."""
        async with self._factory() as session:
            settings = await self._effective(session)
            if not settings.task_digest_enabled:
                return 0
            if quiet_now(settings, now=now):
                return 0
            users = await due_users(session, settings, limit=self._batch, now=now)
            if not users:
                return 0
            payloads: list[tuple[User, DigestPayload]] = []
            for user in users:
                payload = await build_payload(
                    session, user=user, settings=settings, gate=self._gate, bot=self._bot
                )
                if payload.total < max(settings.task_digest_min_tasks, 1):
                    # Nothing to offer: still stamp the user so the empty check is
                    # not repeated on every tick.
                    mark_sent(user, now=now)
                    continue
                payloads.append((user, payload))
            await session.commit()

            sent = 0
            delay = 1.0 / max(settings.task_digest_rate_per_sec, 1)
            for user, payload in payloads:
                if self._stop.is_set():
                    break
                if await self._deliver(session, user=user, payload=payload, now=now):
                    sent += 1
                await asyncio.sleep(delay)
            await session.commit()
            if sent:
                log.info("task_digest_sent", count=sent)
            return sent

    async def _deliver(
        self,
        session: AsyncSession,
        *,
        user: User,
        payload: DigestPayload,
        now: datetime | None,
    ) -> bool:
        await commit_before_io()
        try:
            await self._sender(user.id, payload)
        except TelegramRetryAfter as exc:
            log.info("task_digest_flood", retry_after=exc.retry_after)
            await asyncio.sleep(float(exc.retry_after))
            return False
        except (TelegramForbiddenError, TelegramNotFound):
            # Blocked or deleted: stop bothering them, the audience self-heals.
            user.blocked_bot_at = datetime.now(UTC)
            mark_sent(user, now=now)
            await session.flush()
            return False
        except Exception:
            log.info("task_digest_send_failed", user_id=user.id, exc_info=True)
            return False
        mark_sent(user, now=now)
        await session.flush()
        return True
