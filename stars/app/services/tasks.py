"""Task discovery and validation.

The UI must never advertise a task just because it exists in our configuration.  Every
external task is probed at read time and the exact same checks are repeated when a
reward is claimed.  Providers are isolated so an unavailable provider does not hide
valid tasks from the other providers.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import aiohttp

from app.config import config
from app.services.pricing import get_setting

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskAvailability:
    available: bool
    reason: str | None = None


async def _telegram_request(method: str, payload: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    """Call Telegram without creating an aiogram session for every task probe."""
    token = config.BOT_TOKEN.strip()
    if not token or token.startswith(("1234567890", "7777777777")):
        raise RuntimeError("Telegram Bot API is not configured")

    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.post(
            f"https://api.telegram.org/bot{token}/{method}", json=payload
        ) as response:
            data = await response.json(content_type=None)
            if response.status != 200 or not data.get("ok"):
                description = data.get("description", f"HTTP {response.status}")
                raise RuntimeError(f"Telegram API: {description}")
            return data["result"]


async def channel_task_availability() -> TaskAvailability:
    """Return available only when the configured channel really exists and is reachable."""
    enabled = await get_setting("task_channel_enabled", "0") == "1"
    if not enabled:
        return TaskAvailability(False, "Задание отключено")

    channel_id = (await get_setting("task_channel_id", "")).strip()
    channel_url = (await get_setting("task_channel_url", "")).strip()
    if not channel_id or not channel_url:
        return TaskAvailability(False, "Канал не настроен")

    try:
        chat = await _telegram_request("getChat", {"chat_id": channel_id})
    except Exception as exc:
        logger.warning("Skipping unavailable Telegram task %s: %s", channel_id, exc)
        return TaskAvailability(False, "Telegram не подтвердил доступность канала")

    if chat.get("type") not in {"channel", "supergroup"}:
        return TaskAvailability(False, "Ссылка ведёт не на канал")
    return TaskAvailability(True)


async def is_channel_member(telegram_id: int) -> bool:
    """Strict membership verification. API errors must never grant a paid reward."""
    channel_id = (await get_setting("task_channel_id", "")).strip()
    if not channel_id:
        return False
    member = await _telegram_request(
        "getChatMember", {"chat_id": channel_id, "user_id": telegram_id}
    )
    return member.get("status") not in {"left", "kicked"}


async def discover_external_tasks() -> dict[str, TaskAvailability]:
    """Probe all external task providers concurrently.

    New offerwall/API providers should be added to ``probes``. ``return_exceptions``
    deliberately keeps healthy providers usable if one provider is down.
    """
    probes: dict[str, Callable[[], Awaitable[TaskAvailability]]] = {
        "subscribe_channel": channel_task_availability,
    }
    results = await asyncio.gather(
        *(probe() for probe in probes.values()), return_exceptions=True
    )
    availability: dict[str, TaskAvailability] = {}
    for task_id, result in zip(probes, results):
        if isinstance(result, BaseException):
            logger.exception("Task provider probe failed for %s", task_id, exc_info=result)
            availability[task_id] = TaskAvailability(False, "Провайдер временно недоступен")
        else:
            availability[task_id] = result
    return availability
