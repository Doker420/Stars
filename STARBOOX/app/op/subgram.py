from app.config import Settings
from app.op.base import BoundedCache, OpContext, OpResult, PartnerOffer, Sponsor
from app.op.http import as_dict, as_list, post_json

# Only a definite "not subscribed" may hold the user at the gate. ``notgetted``
# means SubGram itself could not verify the resource (docs: «не удалось проверить»)
# — treating it as blocking left users stuck on «Я подписался» forever.
BLOCKING_STATUSES = frozenset({"unsubscribed"})
# Resource kinds Telegram membership cannot be verified for: SubGram never reports
# them as ``subscribed``, so requiring that would also be an infinite loop.
UNVERIFIABLE_TYPES = frozenset({"resource", "smart_link"})


class SubGramAdapter:
    """SubGram: POST /get-sponsors and POST /get-user-subscriptions (Auth header)."""

    name = "subgram"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._links: BoundedCache[int, list[str]] = BoundedCache()

    def _ready(self) -> bool:
        return bool(self._settings.subgram_api_key.strip())

    def _headers(self) -> dict[str, str]:
        return {
            "Auth": self._settings.subgram_api_key,
            "Content-Type": "application/json",
        }

    def _base(self) -> str:
        return self._settings.subgram_api_url.rstrip("/")

    async def check(self, user: OpContext) -> OpResult:
        if not self._ready():
            return OpResult.skip(self.name, "SUBGRAM_API_KEY не задан")
        try:
            status, payload = await post_json(
                f"{self._base()}/get-sponsors",
                json={
                    "user_id": user.user_id,
                    "chat_id": user.chat_id,
                    "first_name": user.first_name,
                    "username": user.username,
                    "language_code": user.language_code or "ru",
                    "is_premium": user.is_premium,
                    "action": "subscribe",
                    "get_links": 1,
                    "max_sponsors": 8,
                },
                headers=self._headers(),
                timeout_sec=self._settings.op_timeout_sec,
            )
            data = as_dict(payload)
            if status >= 500 or data.get("status") == "error":
                return OpResult.fail_open_result(self.name, str(data.get("message") or status))
            if data.get("status") in {"ok", "success"} and not _unsubscribed(data):
                return OpResult.ok(self.name)
            sponsors = _sponsors_from(data)
            self._links.set(user.user_id, [item.url for item in sponsors])
            if not sponsors:
                # ``warning`` with nothing actionable left (everything is subscribed,
                # unverifiable or withdrawn from rotation). Blocking here would show
                # a sponsor block with no buttons and «Проверить» could never pass.
                return OpResult.ok(self.name)
            return OpResult.blocked(
                self.name,
                sponsors,
                "Подпишитесь на спонсоров SubGram.",
            )
        except Exception as exc:
            return OpResult.fail_open_result(self.name, str(exc))

    # --- rewarded offers ---------------------------------------------------------

    async def offers(self, user: OpContext, limit: int) -> list[PartnerOffer]:
        """``action=newtask`` asks SubGram for extra (non-mandatory) resources."""
        if not self._ready():
            return []
        try:
            status, payload = await post_json(
                f"{self._base()}/get-sponsors",
                json={
                    "user_id": user.user_id,
                    "chat_id": user.chat_id,
                    "first_name": user.first_name,
                    "username": user.username,
                    "language_code": user.language_code or "ru",
                    "is_premium": user.is_premium,
                    "action": "newtask",
                    "get_links": 1,
                    "max_sponsors": max(1, min(limit, 15)),
                },
                headers=self._headers(),
                timeout_sec=self._settings.op_timeout_sec,
            )
        except Exception:
            return []
        data = as_dict(payload)
        if status >= 400 or data.get("status") == "error":
            return []
        offers: list[PartnerOffer] = []
        for item in as_list(data):
            if not isinstance(item, dict):
                continue
            offer = _offer_from(item)
            if offer is not None:
                offers.append(offer)
        return offers

    async def confirm(self, user: OpContext, offer: PartnerOffer) -> bool | None:
        link = str(offer.meta.get("link") or offer.url)
        if not link:
            return None
        try:
            status, payload = await post_json(
                f"{self._base()}/get-user-subscriptions",
                json={"user_id": user.user_id, "links": [link]},
                headers=self._headers(),
                timeout_sec=self._settings.op_timeout_sec,
            )
        except Exception:
            return None
        data = as_dict(payload)
        if status >= 400 or data.get("status") == "error":
            return None
        # ``get-user-subscriptions`` answers either with a bare list or with the
        # usual envelope — normalise both.
        rows = payload if isinstance(payload, list) else as_list(data)
        for item in rows:
            if not isinstance(item, dict):
                continue
            if str(item.get("link") or "") != link:
                continue
            return str(item.get("status")) == "subscribed"
        return None

    async def verify(self, user: OpContext) -> OpResult:
        if not self._ready():
            return OpResult.skip(self.name, "SUBGRAM_API_KEY не задан")
        links = self._links.get(user.user_id) or []
        if not links:
            return await self.check(user)
        try:
            status, payload = await post_json(
                f"{self._base()}/get-user-subscriptions",
                json={"user_id": user.user_id, "links": links},
                headers=self._headers(),
                timeout_sec=self._settings.op_timeout_sec,
            )
            data = as_dict(payload)
            if status >= 500 or data.get("status") == "error":
                return OpResult.fail_open_result(self.name, str(data.get("message") or status))
            rows = payload if isinstance(payload, list) else as_list(data)
            remaining = [
                Sponsor(
                    title=str(
                        item.get("button_text") or item.get("resource_name") or "Спонсор SubGram"
                    ),
                    url=str(item.get("link") or ""),
                    kind=str(item.get("type") or "channel"),
                )
                for item in rows
                if isinstance(item, dict) and _blocking(item) and item.get("link")
            ]
            if remaining:
                return OpResult.blocked(self.name, remaining, "Ещё не все подписки засчитаны.")
            return OpResult.ok(self.name)
        except Exception as exc:
            return OpResult.fail_open_result(self.name, str(exc))


def _offer_from(item: dict) -> PartnerOffer | None:
    url = str(item.get("link") or item.get("url") or "")
    if not url:
        return None
    status = str(item.get("status") or "unsubscribed")
    return PartnerOffer(
        external_id=str(item.get("resource_id") or item.get("id") or url),
        title=str(item.get("resource_name") or item.get("name") or "Задание SubGram"),
        url=url,
        kind=str(item.get("type") or "channel"),
        provider="subgram",
        done=status == "subscribed",
        meta={"link": url},
    )


def _blocking(item: dict) -> bool:
    """Whether this sponsor must still hold the user at the gate."""
    if item.get("available_now") is False:
        # Stopped or rejected by moderation — the docs say not to show it at all.
        return False
    if str(item.get("type") or "").lower() in UNVERIFIABLE_TYPES:
        return False
    return str(item.get("status") or "") in BLOCKING_STATUSES


def _unsubscribed(data: dict) -> bool:
    return any(isinstance(item, dict) and _blocking(item) for item in as_list(data))


def _sponsors_from(data: dict) -> list[Sponsor]:
    sponsors: list[Sponsor] = []
    for item in as_list(data):
        if not isinstance(item, dict):
            continue
        url = str(item.get("link") or item.get("url") or "")
        if not url or not _blocking(item):
            continue
        sponsors.append(
            Sponsor(
                title=str(
                    item.get("button_text") or item.get("resource_name") or "Спонсор SubGram"
                ),
                url=url,
                kind=str(item.get("type") or "channel"),
            )
        )
    return sponsors
