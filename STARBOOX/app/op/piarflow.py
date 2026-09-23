"""PiarFlow OP adapter — https://piarflow.com/api-docs

* ``POST /sponsors`` (``Authorization: Bearer <api key>``) with ``user_id``,
  ``chat_id``, ``max_sponsors`` and — for bots connected without a token — the
  profile fields ``first_name`` / ``username`` / ``language_code`` / ``bio``
  (``null`` when unknown). Returns ``sponsors[] = {link, status, price}``.
* ``POST /sponsors/check`` with ``user_id`` and the ``links`` shown to the user.

Statuses: ``subscribed`` — done, ``not_counted`` — done but not paid (still done
from the user's point of view), ``unsubscribed`` — pending. HTTP 404 means "no
tasks" → pass. 401/429/5xx/transport → fail-open.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.op.base import BoundedCache, OpContext, OpResult, PartnerOffer, Sponsor, title_from_link
from app.op.http import as_dict, as_list, post_json

DONE_STATUSES = frozenset({"subscribed", "not_counted"})


class PiarFlowAdapter:
    name = "piarflow"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._links: BoundedCache[int, list[str]] = BoundedCache()

    def _ready(self) -> bool:
        return bool(self._settings.piarflow_api_key.strip())

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.piarflow_api_key}",
            "Content-Type": "application/json",
        }

    def _base(self) -> str:
        return self._settings.piarflow_api_url.rstrip("/")

    async def check(self, user: OpContext) -> OpResult:
        if not self._ready():
            return OpResult.skip(self.name, "PIARFLOW_API_KEY не задан")
        body: dict[str, Any] = {
            "user_id": user.user_id,
            "chat_id": user.chat_id,
            "max_sponsors": self._settings.piarflow_max_sponsors,
            "first_name": user.first_name or None,
            "username": user.username or None,
            "language_code": user.language_code or None,
            "bio": None,
        }
        try:
            status, payload = await post_json(
                f"{self._base()}/sponsors",
                json=body,
                headers=self._headers(),
                timeout_sec=self._settings.op_timeout_sec,
            )
        except Exception as exc:
            return OpResult.fail_open_result(self.name, str(exc))
        if status == 404:
            return OpResult.ok(self.name)
        data = as_dict(payload)
        if status >= 400 or data.get("status") == "error":
            return OpResult.fail_open_result(self.name, str(data.get("message") or status))
        sponsors = _pending(as_list(data))
        self._links.set(user.user_id, [item.url for item in sponsors])
        if not sponsors:
            return OpResult.ok(self.name)
        return OpResult.blocked(self.name, sponsors, "Выполните задания PiarFlow и нажмите «Я подписался».")

    # --- rewarded offers ---------------------------------------------------------

    async def offers(self, user: OpContext, limit: int) -> list[PartnerOffer]:
        if not self._ready():
            return []
        body: dict[str, Any] = {
            "user_id": user.user_id,
            "chat_id": user.chat_id,
            "max_sponsors": max(1, limit),
            "first_name": user.first_name or None,
            "username": user.username or None,
            "language_code": user.language_code or None,
            "bio": None,
        }
        try:
            status, payload = await post_json(
                f"{self._base()}/sponsors",
                json=body,
                headers=self._headers(),
                timeout_sec=self._settings.op_timeout_sec,
            )
        except Exception:
            return []
        if status == 404:
            return []
        data = as_dict(payload)
        if status >= 400 or data.get("status") == "error":
            return []
        offers: list[PartnerOffer] = []
        for item in as_list(data):
            if not isinstance(item, dict):
                continue
            url = str(item.get("link") or "")
            if not url:
                continue
            offers.append(
                PartnerOffer(
                    external_id=url,
                    title=title_from_link(url, "Задание PiarFlow"),
                    url=url,
                    kind=str(item.get("type") or "channel"),
                    provider=self.name,
                    done=str(item.get("status") or "").lower() in DONE_STATUSES,
                    meta={"link": url},
                )
            )
        return offers

    async def confirm(self, user: OpContext, offer: PartnerOffer) -> bool | None:
        link = str(offer.meta.get("link") or offer.url)
        try:
            status, payload = await post_json(
                f"{self._base()}/sponsors/check",
                json={"user_id": user.user_id, "links": [link]},
                headers=self._headers(),
                timeout_sec=self._settings.op_timeout_sec,
            )
        except Exception:
            return None
        if status == 404:
            return True
        data = as_dict(payload)
        if status >= 400 or data.get("status") == "error":
            return None
        for item in as_list(data):
            if isinstance(item, dict) and str(item.get("link") or "") == link:
                return str(item.get("status") or "").lower() in DONE_STATUSES
        return True

    async def verify(self, user: OpContext) -> OpResult:
        if not self._ready():
            return OpResult.skip(self.name, "PIARFLOW_API_KEY не задан")
        links = self._links.get(user.user_id) or []
        if not links:
            return await self.check(user)
        try:
            status, payload = await post_json(
                f"{self._base()}/sponsors/check",
                json={"user_id": user.user_id, "links": links},
                headers=self._headers(),
                timeout_sec=self._settings.op_timeout_sec,
            )
        except Exception as exc:
            return OpResult.fail_open_result(self.name, str(exc))
        if status == 404:
            self._links.pop(user.user_id)
            return OpResult.ok(self.name)
        data = as_dict(payload)
        if status >= 400 or data.get("status") == "error":
            return OpResult.fail_open_result(self.name, str(data.get("message") or status))
        remaining = _pending(as_list(data))
        if remaining:
            return OpResult.blocked(self.name, remaining, "Ещё не все задания выполнены.")
        self._links.pop(user.user_id)
        return OpResult.ok(self.name)


def _pending(items: list[Any]) -> list[Sponsor]:
    result: list[Sponsor] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("link") or "")
        if not url or str(item.get("status") or "").lower() in DONE_STATUSES:
            continue
        result.append(Sponsor(title=title_from_link(url, "Спонсор PiarFlow"), url=url, kind="channel"))
    return result
