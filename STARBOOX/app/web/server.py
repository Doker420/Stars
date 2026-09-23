"""aiohttp server embedded in the bot process.

Routes:

* ``GET /``            — landing page with a link to the bot
* ``GET /health``      — liveness probe (used by hosting panels)
* ``GET /verify``      — the Mini App page (device verification)
* ``POST /api/device`` — verification callback: signed ``initData`` + fingerprint

The server listens on ``SERVER_PORT`` (RubyHost injects it) and is reachable through
the panel's HTTPS address configured as ``WEB_PUBLIC_URL``.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from aiogram import Bot
from aiogram.types import LabeledPrice
from aiogram.utils.web_app import WebAppInitData, safe_parse_webapp_init_data
from aiohttp import web
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import __version__
from app.config import Settings
from app.db.models import ReferralEdge, User
from app.services import devices, events, games, referrals
from app.services import tasks as task_service
from app.services.app_settings import RuntimeSettingsStore
from app.services.errors import EconomyError
from app.services.events import DomainEvent
from app.web.page import GAME_PAGE, LANDING_PAGE, VERIFY_PAGE

log = structlog.get_logger("kodostars.web")

INIT_DATA_MAX_AGE_SEC = 6 * 3600
RATE_LIMIT_PER_MINUTE = 20
EventsSink = Callable[[list[DomainEvent]], Awaitable[None]]


class RateLimiter:
    def __init__(self, per_minute: int = RATE_LIMIT_PER_MINUTE, max_keys: int = 10_000) -> None:
        self._per_minute = per_minute
        self._max_keys = max_keys
        self._hits: OrderedDict[str, list[float]] = OrderedDict()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = self._hits.setdefault(key, [])
        window[:] = [stamp for stamp in window if now - stamp < 60]
        self._hits.move_to_end(key)
        while len(self._hits) > self._max_keys:
            self._hits.popitem(last=False)
        if len(window) >= self._per_minute:
            return False
        window.append(now)
        return True


def client_ip(request: web.Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:64]
    real = request.headers.get("X-Real-IP", "").strip()
    if real:
        return real[:64]
    return request.remote


def parse_init_data(token: str, init_data: str) -> WebAppInitData:
    """Verify the Mini App signature and freshness; raises ``ValueError`` when invalid."""
    parsed = safe_parse_webapp_init_data(token=token, init_data=init_data)
    auth_date = parsed.auth_date
    if auth_date.tzinfo is None:
        auth_date = auth_date.replace(tzinfo=UTC)
    if (datetime.now(UTC) - auth_date).total_seconds() > INIT_DATA_MAX_AGE_SEC:
        raise ValueError("initData expired")
    if parsed.user is None:
        raise ValueError("initData without user")
    return parsed


class WebServer:
    def __init__(
        self,
        *,
        bot: Bot,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        settings_store: RuntimeSettingsStore,
        bot_username: str,
        events_sink: EventsSink | None = None,
    ) -> None:
        self._bot = bot
        self._factory = session_factory
        self._settings = settings
        self._store = settings_store
        self._bot_username = bot_username
        self._events_sink = events_sink
        self._limiter = RateLimiter()
        self._runner: web.AppRunner | None = None

    def build_app(self) -> web.Application:
        app = web.Application(client_max_size=64 * 1024)
        app.router.add_get("/", self.landing)
        app.router.add_get("/health", self.health)
        app.router.add_get("/verify", self.verify_page)
        app.router.add_get("/app", self.game_page)
        app.router.add_post("/api/game/profile", self.api_game_profile)
        app.router.add_post("/api/game/case", self.api_open_case)
        app.router.add_post("/api/game/spin", self.api_spin)
        app.router.add_post("/api/game/invoice", self.api_game_invoice)
        app.router.add_post("/api/device", self.api_device)
        return app

    async def start(self, host: str, port: int) -> None:
        self._runner = web.AppRunner(self.build_app(), access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, host, port).start()
        log.info("web_started", host=host, port=port, public_url=self._settings.web_public_url or None)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    # --- handlers ---------------------------------------------------------------------

    async def landing(self, request: web.Request) -> web.Response:
        html = LANDING_PAGE.replace("{username}", self._bot_username)
        return web.Response(text=html, content_type="text/html", charset="utf-8")

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "version": __version__})

    async def verify_page(self, request: web.Request) -> web.Response:
        return web.Response(
            text=VERIFY_PAGE,
            content_type="text/html",
            charset="utf-8",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
        )

    async def game_page(self, request: web.Request) -> web.Response:
        html = GAME_PAGE.replace("{username}", self._bot_username)
        return web.Response(
            text=html,
            content_type="text/html",
            charset="utf-8",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
        )

    async def _game_request(self, request: web.Request) -> tuple[dict[str, Any], WebAppInitData] | web.Response:
        ip = client_ip(request)
        if not self._limiter.allow(f"game:{ip or 'unknown'}"):
            return web.json_response({"error": "Слишком много запросов"}, status=429)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError
            init = parse_init_data(self._settings.bot_token, str(body.get("initData") or ""))
        except Exception:
            return web.json_response({"error": "Подпись Telegram не подтверждена"}, status=401)
        return body, init

    async def api_game_profile(self, request: web.Request) -> web.Response:
        parsed = await self._game_request(request)
        if isinstance(parsed, web.Response):
            return parsed
        _, init = parsed
        async with self._factory() as session:
            user = await session.get(User, init.user.id)
            if user is None:
                return web.json_response({"error": "Сначала запустите бота"}, status=404)
            user.is_premium = bool(init.user.is_premium)
            refs = int((await session.execute(select(func.count()).select_from(ReferralEdge).where(ReferralEdge.referrer_id == user.id, ReferralEdge.level == 1))).scalar_one())
            await session.commit()
            cases = [{k: v for k, v in item.items() if k != "rewards"} | {"slug": slug} for slug, item in games.CASES.items()]
            return web.json_response({"user": {"id": user.id, "name": user.display_name, "balance": user.balance, "xp": user.xp, "level": user.level, "streak": user.streak, "keys": user.case_keys, "spins": user.spins, "premium": user.is_premium, "vip_until": user.vip_until.isoformat() if user.vip_until else None, "free_case_available": user.last_free_case_on != datetime.now(UTC).date()}, "referrals": refs, "cases": cases})

    async def api_open_case(self, request: web.Request) -> web.Response:
        parsed = await self._game_request(request)
        if isinstance(parsed, web.Response):
            return parsed
        body, init = parsed
        async with self._factory() as session:
            user = await session.get(User, init.user.id)
            if user is None:
                return web.json_response({"error": "Сначала запустите бота"}, status=404)
            try:
                row = await games.open_case(session, user=user, case_slug=str(body.get("case") or ""), payment=str(body.get("payment") or "key"), request_id=str(body.get("requestId") or ""))
                await session.commit()
            except EconomyError as exc:
                await session.rollback()
                return web.json_response({"error": exc.message}, status=400)
        return web.json_response({"ok": True, "reward": {"kind": row.reward_kind, "amount": row.reward_amount, "status": row.fulfillment_status}})

    async def api_spin(self, request: web.Request) -> web.Response:
        parsed = await self._game_request(request)
        if isinstance(parsed, web.Response):
            return parsed
        body, init = parsed
        async with self._factory() as session:
            user = await session.get(User, init.user.id)
            if user is None:
                return web.json_response({"error": "Сначала запустите бота"}, status=404)
            try:
                row = await games.spin_wheel(session, user=user, request_id=str(body.get("requestId") or ""))
                await session.commit()
            except EconomyError as exc:
                await session.rollback()
                return web.json_response({"error": exc.message}, status=400)
        return web.json_response({"ok": True, "reward": {"kind": row.reward_kind, "amount": row.reward_amount}})

    async def api_game_invoice(self, request: web.Request) -> web.Response:
        parsed = await self._game_request(request)
        if isinstance(parsed, web.Response):
            return parsed
        body, init = parsed
        slug = str(body.get("product") or "")
        product = games.game_product(slug)
        if product is None:
            return web.json_response({"error": "Товар не найден"}, status=404)
        try:
            link = await self._bot.create_invoice_link(
                title=str(product["title"])[:32],
                description="Покупка для игрового раздела STARBOOX",
                payload=f"game:{slug}:{init.user.id}",
                currency="XTR",
                prices=[LabeledPrice(label=str(product["title"])[:32], amount=int(product["xtr"]))],
            )
        except Exception:
            log.warning("game_invoice_failed", product=slug, user_id=init.user.id, exc_info=True)
            return web.json_response({"error": "Не удалось создать счёт"}, status=502)
        return web.json_response({"ok": True, "invoice": link})

    async def api_device(self, request: web.Request) -> web.Response:
        ip = client_ip(request)
        if not self._limiter.allow(ip or "unknown"):
            return web.json_response({"error": "Слишком много запросов"}, status=429)
        try:
            body: dict[str, Any] = await request.json()
        except Exception:
            return web.json_response({"error": "Некорректный JSON"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "Некорректный JSON"}, status=400)

        try:
            init = parse_init_data(self._settings.bot_token, str(body.get("initData") or ""))
        except ValueError as exc:
            log.info("web_init_data_rejected", ip=ip, error=str(exc))
            return web.json_response({"error": "Подпись Telegram не подтверждена"}, status=401)

        signals = body.get("signals") if isinstance(body.get("signals"), dict) else {}
        fingerprint = str(body.get("fingerprint") or "") or devices.fingerprint_from_signals(signals)
        pending: list[DomainEvent] = []
        async with self._factory() as session:
            settings = await self._store.effective(session)
            if not settings.device_check_active:
                return web.json_response({"error": "Проверка устройства выключена"}, status=404)
            user = await session.get(User, init.user.id)
            if user is None:
                return web.json_response({"error": "Сначала запустите бота"}, status=404)
            if user.is_banned:
                return web.json_response({"error": "Аккаунт заблокирован"}, status=403)
            try:
                verdict = await devices.register_device(
                    session,
                    user=user,
                    fingerprint=fingerprint,
                    ip=ip,
                    user_agent=request.headers.get("User-Agent", "")[:512],
                    platform=str(signals.get("tg_platform") or "")[:32] or None,
                    tg_version=str(signals.get("tg_version") or "")[:16] or None,
                    signals=signals,
                    settings=settings,
                )
                # Verification may be the last missing piece for the referral bonus.
                await referrals.activate_if_ready(session, user=user, settings=settings)
                if user.referred_by_id and user.referral_activated:
                    referrer = await session.get(User, user.referred_by_id)
                    if referrer is not None and not referrer.is_banned:
                        await task_service.try_complete_event(
                            session, user=referrer, event="invite_activated", settings=settings
                        )
            except EconomyError as exc:
                await session.rollback()
                return web.json_response({"error": exc.message}, status=400)
            await session.commit()
            pending = events.drain(session)
        if pending and self._events_sink is not None:
            try:
                await self._events_sink(pending)
            except Exception:
                log.warning("web_events_dispatch_failed", exc_info=True)
        log.info(
            "device_verified",
            user_id=init.user.id,
            twink=verdict.twink,
            matches=len(verdict.matched_user_ids),
            ip=ip,
        )
        return web.json_response({"ok": True, "twink": verdict.twink, "first_time": verdict.first_time})
