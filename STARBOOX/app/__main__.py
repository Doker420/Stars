from __future__ import annotations

import asyncio
import contextlib
import os
import signal

import structlog
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError

from app import __version__
from app.bot.commands import setup_commands
from app.bot.factory import (
    create_bot,
    create_dispatcher,
    make_broadcast_sender,
    make_digest_sender,
)
from app.bot.notify import Notifier
from app.config import get_settings
from app.db.migrate import run_migrations
from app.db.seed import seed_catalog
from app.db.session import create_engine, create_session_factory
from app.log_setup import setup_logging
from app.op.gate import OpGate
from app.op.http import close_session
from app.services.access import AccessRegistry
from app.services.app_settings import RuntimeSettingsStore
from app.services.broadcasts import BroadcastRunner
from app.services.task_digest import TaskDigestRunner
from app.web.server import WebServer


async def idle_until_signal(log) -> None:
    """Stay alive but killable.

    Used when the bot cannot work (bad token) yet the process must not crash-loop
    the hosting panel. A bare ``asyncio.Event().wait()`` here was unkillable: at this
    point nothing has installed signal handlers, so the default SIGTERM disposition
    is replaced by asyncio's "ignore while the loop runs", and the panel's Stop /
    Restart button (SIGTERM) did nothing — the process had to be SIGKILLed.
    """
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    installed: list[int] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
            installed.append(sig)
        except (NotImplementedError, RuntimeError):  # pragma: no cover - non-POSIX
            pass
    try:
        await stop.wait()
    finally:
        for sig in installed:
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.remove_signal_handler(sig)
    log.info("stopped_by_signal")


async def run() -> None:
    settings = get_settings()
    setup_logging(settings)
    log = structlog.get_logger("kodostars")
    log.info("starting", version=__version__, database=settings.database_url, admins=len(settings.admin_ids))
    if not settings.admin_ids:
        log.warning("no_admins_configured", hint="Задайте ADMIN_IDS в .env, иначе админ-панель недоступна.")

    engine = create_engine(settings)
    await run_migrations(engine, settings)
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        await seed_catalog(session)

    access = AccessRegistry(settings.admin_ids)
    async with session_factory() as session:
        await access.load(session)
    settings_store = RuntimeSettingsStore(settings)
    op_gate = OpGate(settings)
    bot = create_bot(settings)

    bot_username = "bot"
    if settings.is_placeholder_token:
        log.warning(
            "bot_token_is_placeholder",
            hint="Задайте BOT_TOKEN в .env. Polling всё равно запускается.",
        )
    else:
        try:
            # Bounded: the default aiogram session timeout is 60s, and a blocked
            # outbound connection (firewall, no IPv4 route to api.telegram.org)
            # would otherwise make startup look like a freeze.
            me = await asyncio.wait_for(bot.get_me(), timeout=settings.startup_api_timeout_sec)
            bot_username = me.username or bot_username
        except TelegramUnauthorizedError:
            log.error("telegram_unauthorized", hint="BOT_TOKEN неверный. Процесс ждёт исправления токена.")
            await bot.session.close()
            await engine.dispose()
            await idle_until_signal(log)
            return
        except TimeoutError:
            log.warning(
                "get_me_timeout",
                timeout=settings.startup_api_timeout_sec,
                hint="Telegram API не отвечает. Проверьте сеть/прокси. Polling всё равно запускается.",
            )
        except TelegramAPIError as exc:
            log.warning("get_me_failed", error=str(exc))

    notifier = Notifier(bot, session_factory, access)
    async with session_factory() as session:
        effective = await settings_store.effective(session)
    broadcast_runner = BroadcastRunner(
        session_factory,
        sender=make_broadcast_sender(bot),
        rate_per_sec=effective.broadcast_rate_per_sec,
        progress_callback=notifier.broadcast_progress,
    )
    digest_runner = TaskDigestRunner(
        session_factory,
        sender=make_digest_sender(bot),
        settings=settings,
        settings_provider=settings_store.effective,
        gate=op_gate,
        bot=bot,
    )

    dp = create_dispatcher(
        settings,
        session_factory,
        op_gate,
        access=access,
        settings_store=settings_store,
        notifier=notifier,
        broadcast_runner=broadcast_runner,
        bot_username=bot_username,
    )
    if not settings.is_placeholder_token:
        # One slow admin chat must not hold the whole boot sequence.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                setup_commands(bot, access.all_admin_ids()),
                timeout=settings.startup_api_timeout_sec,
            )

    # Mini App + health endpoint. Started whenever the hosting panel hands us a port
    # (RubyHost: SERVER_PORT) or WEB_PUBLIC_URL is configured.
    web_server: WebServer | None = None
    if settings.web_enabled or "SERVER_PORT" in os.environ:
        web_server = WebServer(
            bot=bot,
            session_factory=session_factory,
            settings=settings,
            settings_store=settings_store,
            bot_username=bot_username,
            events_sink=notifier.dispatch_events,
        )
        try:
            await web_server.start(settings.web_host, settings.server_port)
        except OSError as exc:
            log.error("web_start_failed", port=settings.server_port, error=str(exc))
            web_server = None
        if settings.web_enabled and effective.device_check_enabled:
            log.info("device_check_enabled", url=settings.web_url("verify"))
        elif not settings.web_enabled:
            log.warning(
                "web_public_url_missing",
                hint="Задайте WEB_PUBLIC_URL (HTTPS-адрес панели), чтобы включить антитвинк Mini App.",
            )

    if not settings.is_placeholder_token:
        digest_runner.start()

    log.info("starting_polling", bot=bot_username, placeholder_token=settings.is_placeholder_token)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except TelegramUnauthorizedError:
        log.error("telegram_unauthorized", placeholder_token=settings.is_placeholder_token)
        if settings.is_placeholder_token:
            log.warning(
                "idle_after_placeholder_token",
                hint="Процесс остаётся запущенным. Подставьте настоящий BOT_TOKEN и перезапустите.",
            )
            await idle_until_signal(log)
        else:
            raise
    finally:
        if web_server is not None:
            await web_server.stop()
        await digest_runner.shutdown()
        await broadcast_runner.shutdown()
        await close_session()
        await bot.session.close()
        await engine.dispose()
        log.info("stopped")


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(run())


if __name__ == "__main__":
    main()
