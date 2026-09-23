from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from aiogram import Bot

if TYPE_CHECKING:
    from app.config import Settings


@dataclass(slots=True)
class Sponsor:
    title: str
    url: str
    kind: str = "channel"


@dataclass(slots=True)
class OpResult:
    allowed: bool
    provider: str
    skipped: bool = False
    fail_open: bool = False
    message: str = ""
    sponsors: list[Sponsor] = field(default_factory=list)
    # The provider already sent its own subscription block to the user (Flyer `sub`
    # keys do this), so an empty ``sponsors`` list here is still actionable.
    self_served: bool = False

    @classmethod
    def ok(cls, provider: str, message: str = "") -> OpResult:
        return cls(allowed=True, provider=provider, message=message)

    @classmethod
    def blocked(
        cls,
        provider: str,
        sponsors: list[Sponsor],
        message: str = "",
        *,
        self_served: bool = False,
    ) -> OpResult:
        return cls(
            allowed=False,
            provider=provider,
            sponsors=sponsors,
            message=message,
            self_served=self_served,
        )

    @classmethod
    def skip(cls, provider: str, reason: str) -> OpResult:
        return cls(allowed=True, provider=provider, skipped=True, message=reason)

    @classmethod
    def fail_open_result(cls, provider: str, error: str) -> OpResult:
        return cls(allowed=True, provider=provider, fail_open=True, message=error)


@dataclass(slots=True)
class PartnerOffer:
    """An *optional* (rewarded) task exposed by a monetization provider.

    Unlike :class:`Sponsor` (used by the mandatory-subscription gate) an offer is
    something the user may take for an internal Stars reward. ``external_id`` must be
    stable per provider so completions stay idempotent.
    """

    external_id: str
    title: str
    url: str
    kind: str = "channel"
    provider: str = ""
    description: str = ""
    done: bool = False
    # Provider-specific token needed to confirm the completion (Flyer signature, …).
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OpContext:
    user_id: int
    chat_id: int
    first_name: str
    username: str | None
    language_code: str
    is_premium: bool
    bot: Bot | None = None
    settings: Settings | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class OpAdapter(Protocol):
    name: str

    async def check(self, user: OpContext) -> OpResult: ...

    async def verify(self, user: OpContext) -> OpResult: ...


class OfferProvider(Protocol):
    """Optional capability: a provider that can also serve *rewarded* tasks."""

    name: str

    async def offers(self, user: OpContext, limit: int) -> list[PartnerOffer]: ...

    async def confirm(self, user: OpContext, offer: PartnerOffer) -> bool | None:
        """True = done, False = not done, None = cannot tell (caller decides)."""
        ...


def title_from_link(url: str, fallback: str) -> str:
    """``https://t.me/cryptonews?start=x`` → ``@cryptonews``; anything else → fallback."""
    for prefix in (
        "https://t.me/",
        "http://t.me/",
        "https://telegram.me/",
        "http://telegram.me/",
        "tg://resolve?domain=",
    ):
        if url.startswith(prefix):
            handle = url[len(prefix) :].split("?", 1)[0].split("/", 1)[0].strip()
            if handle.startswith("+") or handle in {"", "c", "joinchat", "addlist"}:
                return fallback
            return f"@{handle}"
    return fallback


class BoundedCache[K, V]:
    """Tiny LRU used by adapters to remember sponsor links per user."""

    def __init__(self, max_size: int = 10_000) -> None:
        self._max = max_size
        self._data: OrderedDict[K, V] = OrderedDict()

    def get(self, key: K, default: V | None = None) -> V | None:
        value = self._data.get(key)
        if value is None:
            return default
        self._data.move_to_end(key)
        return value

    def set(self, key: K, value: V) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def pop(self, key: K) -> None:
        self._data.pop(key, None)

    def __len__(self) -> int:
        return len(self._data)
