"""Flyer OP adapter — https://api.flyerhubs.com/

Flyer issues bot keys of different *types*:

* ``sub``   — mandatory subscription. ``POST /check`` returns ``skip`` (``true`` →
  let the user through). When ``skip`` is ``false`` Flyer itself sends the
  subscription message with buttons to the user (the ``message`` object only
  customises its look), so we return a blocked result **without** sponsors and
  just ask the user to press «Я подписался» afterwards.
* ``tasks`` — tasks. ``POST /get_tasks`` returns ``result[]`` with ``signature``,
  ``task``, ``links[]``, ``name`` and ``status`` (``incomplete`` / ``abort`` mean
  not done; ``waiting`` / ``complete`` mean done; ``unavailable`` is stale).

The key type is resolved once through ``POST /get_me`` and cached. Missing key →
skip; API/transport errors → fail-open.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.op.base import OpContext, OpResult, PartnerOffer, Sponsor
from app.op.http import as_dict, as_list, post_json

PENDING_TASK_STATUSES = frozenset({"incomplete", "abort"})
DONE_TASK_STATUSES = frozenset({"waiting", "complete", "completed"})
TASK_KINDS = {
    "start bot": "bot",
    "subscribe channel": "channel",
    "give boost": "boost",
    "follow link": "resource",
    "perform action": "resource",
    "view posts": "channel",
}
TASK_TITLES = {
    "start bot": "Запустить бота",
    "subscribe channel": "Подписаться на канал",
    "give boost": "Дать буст каналу",
    "follow link": "Перейти по ссылке",
    "perform action": "Выполнить действие",
    "view posts": "Посмотреть посты",
}
SUB_MESSAGE = {
    "rows": 1,
    "text": "🔒 <b>Обязательная подписка</b>\n\nПодпишитесь на спонсоров, затем вернитесь в бота "
    "и нажмите «Я подписался».",
    "button_channel": "➕ Подписаться",
    "button_bot": "🤖 Запустить бота",
    "button_boost": "⚡ Дать буст",
    "button_url": "🔗 Перейти",
    "button_fp": "🔗 Открыть",
}


class FlyerAdapter:
    name = "flyer"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._key_type: str | None = None

    def _ready(self) -> bool:
        return bool(self._settings.flyer_api_key.strip())

    def _url(self, path: str) -> str:
        return f"{self._settings.flyer_api_url.rstrip('/')}/{path.lstrip('/')}"

    async def _post(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        status, payload = await post_json(
            self._url(path),
            json={"key": self._settings.flyer_api_key, **body},
            timeout_sec=self._settings.op_timeout_sec,
        )
        return status, as_dict(payload)

    async def _resolve_key_type(self) -> str | None:
        if self._key_type is not None:
            return self._key_type
        try:
            status, data = await self._post("/get_me", {})
        except Exception:
            return None
        if status >= 400 or data.get("error"):
            return None
        key_type = str(data.get("type") or "").lower()
        if key_type in {"sub", "tasks"}:
            self._key_type = key_type
        return self._key_type

    async def check(self, user: OpContext) -> OpResult:
        if not self._ready():
            return OpResult.skip(self.name, "FLYER_API_KEY не задан")
        key_type = await self._resolve_key_type()
        if key_type == "tasks":
            return await self._check_tasks(user)
        result = await self._check_sub(user)
        if result.fail_open and "prohibited" in result.message.lower():
            # Wrong method for this key type: it must be a tasks key.
            self._key_type = "tasks"
            return await self._check_tasks(user)
        return result

    async def verify(self, user: OpContext) -> OpResult:
        return await self.check(user)

    async def _check_sub(self, user: OpContext) -> OpResult:
        try:
            status, data = await self._post(
                "/check",
                {
                    "user_id": user.user_id,
                    "language_code": user.language_code or "ru",
                    "message": SUB_MESSAGE,
                },
            )
        except Exception as exc:
            return OpResult.fail_open_result(self.name, str(exc))
        if status >= 400 or data.get("error"):
            return OpResult.fail_open_result(self.name, str(data.get("error") or status))
        if data.get("skip") is True:
            return OpResult.ok(self.name)
        return OpResult.blocked(
            self.name,
            [],
            "Flyer отправил список спонсоров отдельным сообщением выше. "
            "Подпишитесь и нажмите «Я подписался».",
            # Flyer pushed its own message: an empty sponsor list is expected here.
            self_served=True,
        )

    # --- rewarded offers ---------------------------------------------------------

    async def offers(self, user: OpContext, limit: int) -> list[PartnerOffer]:
        """Flyer `tasks` keys expose the offer list directly; `sub` keys have none."""
        if not self._ready():
            return []
        if await self._resolve_key_type() == "sub":
            return []
        try:
            status, data = await self._post(
                "/get_tasks",
                {
                    "user_id": user.user_id,
                    "language_code": user.language_code or "ru",
                    "limit": max(1, min(limit, 10)),
                },
            )
        except Exception:
            return []
        if status >= 400 or data.get("error"):
            return []
        offers: list[PartnerOffer] = []
        for item in as_list(data):
            if not isinstance(item, dict):
                continue
            offer = _task_offer(item)
            if offer is not None:
                offers.append(offer)
        return offers

    async def confirm(self, user: OpContext, offer: PartnerOffer) -> bool | None:
        signature = str(offer.meta.get("signature") or "")
        if not signature:
            return None
        try:
            status, data = await self._post(
                "/check_task",
                {"user_id": user.user_id, "signature": signature},
            )
        except Exception:
            return None
        if status >= 400 or data.get("error"):
            # Older keys do not expose /check_task — re-read the task list instead.
            fresh = await self.offers(user, 10)
            for item in fresh:
                if item.external_id == offer.external_id:
                    return item.done
            return None
        result = data.get("result")
        if isinstance(result, dict):
            return str(result.get("status") or "").lower() in DONE_TASK_STATUSES
        if isinstance(data.get("status"), str):
            return data["status"].lower() in DONE_TASK_STATUSES
        return bool(data.get("complete") or data.get("completed"))

    async def _check_tasks(self, user: OpContext) -> OpResult:
        try:
            status, data = await self._post(
                "/get_tasks",
                {
                    "user_id": user.user_id,
                    "language_code": user.language_code or "ru",
                    "limit": max(1, min(self._settings.flyer_tasks_limit, 10)),
                },
            )
        except Exception as exc:
            return OpResult.fail_open_result(self.name, str(exc))
        if status >= 400 or data.get("error"):
            return OpResult.fail_open_result(self.name, str(data.get("error") or status))
        pending = [
            sponsor
            for item in as_list(data)
            if isinstance(item, dict) and (sponsor := _task_sponsor(item)) is not None
        ]
        if not pending:
            return OpResult.ok(self.name)
        return OpResult.blocked(self.name, pending, "Выполните задания Flyer и нажмите «Я подписался».")


def _task_sponsor(item: dict[str, Any]) -> Sponsor | None:
    if str(item.get("status") or "incomplete").lower() not in PENDING_TASK_STATUSES:
        return None
    links = item.get("links")
    url = ""
    if isinstance(links, list) and links:
        url = str(links[0] or "")
    elif isinstance(links, str):
        url = links
    if not url:
        return None
    task = str(item.get("task") or "").lower()
    title = str(item.get("name") or TASK_TITLES.get(task) or "Спонсор Flyer")
    return Sponsor(title=title, url=url, kind=TASK_KINDS.get(task, "channel"))


def _task_offer(item: dict[str, Any]) -> PartnerOffer | None:
    """Build a rewarded offer from one Flyer `result[]` entry."""
    status = str(item.get("status") or "incomplete").lower()
    if status == "unavailable":
        return None
    links = item.get("links")
    url = ""
    if isinstance(links, list) and links:
        url = str(links[0] or "")
    elif isinstance(links, str):
        url = links
    if not url:
        return None
    signature = str(item.get("signature") or "")
    task = str(item.get("task") or "").lower()
    title = str(item.get("name") or TASK_TITLES.get(task) or "Задание Flyer")
    return PartnerOffer(
        external_id=signature or url,
        title=title,
        url=url,
        kind=TASK_KINDS.get(task, "channel"),
        provider="flyer",
        done=status in DONE_TASK_STATUSES,
        meta={"signature": signature} if signature else {},
    )
