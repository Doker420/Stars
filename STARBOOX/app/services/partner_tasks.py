"""Rewarded tasks served by the monetization providers (Flyer, SubGram, …).

The OP cascade is a *gate*: it blocks a user until the mandatory subscriptions are
done. The same provider APIs additionally expose optional offers, and this module
turns them into paid tasks inside the bot:

1. :func:`list_offers` asks every enabled provider for offers, drops the ones this
   user already got paid for, and prices each one with the fixed rate from
   ``Settings.partner_reward`` (level/boost multipliers apply on payout).
2. The user taps an offer → :func:`reserve` stores a ``pending`` claim so the URL
   and the price stay stable while they go and do it.
3. «Проверить» → :func:`complete` re-asks the provider. Only a definite *yes*
   credits the ledger, and the unique ``(user, provider, external_id)`` index makes
   double payouts impossible even under concurrent taps.

Providers are best-effort by design: an API that errors out contributes no offers
and never blocks the screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import LedgerKind, PartnerTaskClaim, PartnerTaskStatus, User
from app.op.base import BoundedCache, OpContext, PartnerOffer
from app.op.gate import PROVIDER_TITLES, OpGate
from app.services import events, ledger, referrals
from app.services.antifraud import bump_activity, ensure_action_cooldown, ensure_not_banned
from app.services.boosts import active_multiplier_bp
from app.services.errors import AlreadyClaimed, EconomyError, NotFound
from app.services.levels import XP_TASK, add_xp, apply_multipliers, info_for_xp

OFFER_KIND_EMOJI: dict[str, str] = {
    "channel": "📢",
    "bot": "🤖",
    "boost": "⚡",
    "resource": "🔗",
    "folder": "📂",
}

OFFER_KIND_ACTIONS: dict[str, str] = {
    "channel": "Подпишитесь на канал",
    "bot": "Запустите бота и не выходите",
    "boost": "Дайте буст каналу",
    "resource": "Перейдите по ссылке",
    "folder": "Добавьте папку",
}


@dataclass(slots=True)
class OfferView:
    """A partner offer priced for a concrete user."""

    offer: PartnerOffer
    reward: int
    claim_id: int | None = None

    @property
    def provider_title(self) -> str:
        return PROVIDER_TITLES.get(self.offer.provider, self.offer.provider.title())

    @property
    def emoji(self) -> str:
        return OFFER_KIND_EMOJI.get(self.offer.kind, "🔗")

    @property
    def action_hint(self) -> str:
        return OFFER_KIND_ACTIONS.get(self.offer.kind, "Выполните задание")


def build_context(user: User, *, chat_id: int | None = None, bot=None) -> OpContext:
    return OpContext(
        user_id=user.id,
        chat_id=chat_id if chat_id is not None else user.id,
        first_name=user.first_name or "",
        username=user.username,
        language_code=user.language_code or "ru",
        is_premium=bool(user.is_premium),
        bot=bot,
    )


async def paid_keys(session: AsyncSession, user_id: int) -> set[tuple[str, str]]:
    """``(provider, external_id)`` pairs this user has already been paid for."""
    rows = await session.execute(
        select(PartnerTaskClaim.provider, PartnerTaskClaim.external_id).where(
            PartnerTaskClaim.user_id == user_id,
            PartnerTaskClaim.status == PartnerTaskStatus.DONE.value,
        )
    )
    return {(str(provider), str(external)) for provider, external in rows.all()}


async def get_claim(session: AsyncSession, claim_id: int, user_id: int) -> PartnerTaskClaim:
    claim = await session.get(PartnerTaskClaim, claim_id)
    if claim is None or claim.user_id != user_id:
        raise NotFound("Задание не найдено")
    return claim


# Last known offer count per user, refreshed whenever offers are fetched for real.
# The main menu uses it to advertise the paid section without adding a provider
# round trip to every /start. Bounded so it cannot grow without limit.
_offer_counts: BoundedCache[int, int] = BoundedCache()


def cached_offer_count(user_id: int) -> int:
    """Best-effort count for the menu badge; 0 when nothing is known yet."""
    return _offer_counts.get(user_id) or 0


async def list_offers(
    session: AsyncSession,
    *,
    user: User,
    gate: OpGate,
    settings: Settings,
    bot=None,
    chat_id: int | None = None,
) -> list[OfferView]:
    """Fresh offers for the user, already priced and de-duplicated against payouts."""
    if not settings.partner_tasks_enabled:
        _offer_counts.set(user.id, 0)
        return []
    limit = max(1, min(settings.partner_tasks_limit, 25))
    ctx = build_context(user, chat_id=chat_id, bot=bot)
    offers = await gate.collect_offers(ctx, session, limit=limit, settings=settings)
    if not offers:
        _offer_counts.set(user.id, 0)
        return []

    paid = await paid_keys(session, user.id)
    pending = await _pending_map(session, user.id)
    views: list[OfferView] = []
    seen: set[tuple[str, str]] = set()
    for offer in offers:
        key = (offer.provider, offer.external_id)
        if key in paid or key in seen:
            continue
        seen.add(key)
        reward = settings.partner_reward(offer.kind)
        if reward <= 0:
            continue
        claim = pending.get(key)
        if claim is not None:
            # Keep the already-quoted price: the user planned around it.
            reward = claim.reward or reward
        views.append(OfferView(offer=offer, reward=reward, claim_id=claim.id if claim else None))
    _offer_counts.set(user.id, len(views))
    return views


async def _pending_map(session: AsyncSession, user_id: int) -> dict[tuple[str, str], PartnerTaskClaim]:
    rows = await session.execute(
        select(PartnerTaskClaim).where(
            PartnerTaskClaim.user_id == user_id,
            PartnerTaskClaim.status == PartnerTaskStatus.PENDING.value,
        )
    )
    return {(row.provider, row.external_id): row for row in rows.scalars().all()}


async def reserve(
    session: AsyncSession,
    *,
    user: User,
    offer: PartnerOffer,
    settings: Settings,
) -> PartnerTaskClaim:
    """Persist the offer so the card survives the round trip to the sponsor."""
    ensure_not_banned(user)
    existing = await session.execute(
        select(PartnerTaskClaim).where(
            PartnerTaskClaim.user_id == user.id,
            PartnerTaskClaim.provider == offer.provider,
            PartnerTaskClaim.external_id == offer.external_id,
        )
    )
    claim = existing.scalar_one_or_none()
    if claim is not None:
        if claim.status == PartnerTaskStatus.DONE.value:
            raise AlreadyClaimed("Награда за это задание уже получена")
        claim.title = offer.title[:160]
        claim.url = offer.url[:512]
        claim.kind = offer.kind
        claim.status = PartnerTaskStatus.PENDING.value
        await session.flush()
        return claim

    claim = PartnerTaskClaim(
        user_id=user.id,
        provider=offer.provider,
        external_id=offer.external_id[:128],
        kind=offer.kind,
        title=offer.title[:160],
        url=offer.url[:512],
        reward=settings.partner_reward(offer.kind),
        status=PartnerTaskStatus.PENDING.value,
    )
    session.add(claim)
    try:
        await session.flush()
    except IntegrityError:
        # Concurrent taps on the same offer: reuse the row the other one created.
        await session.rollback()
        again = await session.execute(
            select(PartnerTaskClaim).where(
                PartnerTaskClaim.user_id == user.id,
                PartnerTaskClaim.provider == offer.provider,
                PartnerTaskClaim.external_id == offer.external_id,
            )
        )
        found = again.scalar_one_or_none()
        if found is None:
            raise
        return found
    return claim


def claim_to_offer(claim: PartnerTaskClaim) -> PartnerOffer:
    meta: dict = {"link": claim.url}
    if claim.provider == "trafsly" and claim.external_id.isdigit():
        meta["ads_id"] = int(claim.external_id)
    if claim.provider == "flyer":
        meta["signature"] = claim.external_id
    return PartnerOffer(
        external_id=claim.external_id,
        title=claim.title,
        url=claim.url,
        kind=claim.kind,
        provider=claim.provider,
        meta=meta,
    )


async def complete(
    session: AsyncSession,
    *,
    user: User,
    claim: PartnerTaskClaim,
    gate: OpGate,
    settings: Settings,
    bot=None,
    chat_id: int | None = None,
) -> int:
    """Verify with the provider and pay out. Returns the credited amount."""
    ensure_not_banned(user)
    if claim.status == PartnerTaskStatus.DONE.value:
        raise AlreadyClaimed("Награда за это задание уже получена")
    ensure_action_cooldown(user, settings)

    ctx = build_context(user, chat_id=chat_id, bot=bot)
    verdict = await gate.confirm_offer(ctx, claim_to_offer(claim), settings=settings)
    if verdict is False:
        raise EconomyError("Задание ещё не засчитано. Выполните его и нажмите «Проверить» снова.")
    if verdict is None:
        raise EconomyError("Партнёр пока не отвечает. Попробуйте проверить через минуту.")

    # Re-read under the current transaction: another update may have paid it already.
    fresh = await session.get(PartnerTaskClaim, claim.id, with_for_update=False)
    if fresh is not None and fresh.status == PartnerTaskStatus.DONE.value:
        raise AlreadyClaimed("Награда за это задание уже получена")

    base = claim.reward or settings.partner_reward(claim.kind)
    level_bp = info_for_xp(user.xp).multiplier_bp
    boost_bp = await active_multiplier_bp(session, user.id)
    amount = apply_multipliers(base, level_bp, boost_bp)

    claim.status = PartnerTaskStatus.DONE.value
    claim.completed_at = datetime.now(UTC)
    await session.flush()

    await ledger.credit(
        session,
        user_id=user.id,
        amount=amount,
        kind=LedgerKind.PARTNER_TASK,
        reference=f"partner:{claim.provider}:{claim.id}"[:64],
        extra={"provider": claim.provider, "external_id": claim.external_id, "base": base},
    )
    await add_xp(session, user, XP_TASK)
    await bump_activity(session, user, 1)
    await referrals.activate_if_ready(session, user=user, settings=settings, boost_bp=boost_bp)
    await referrals.share_earning(
        session,
        earner=user,
        base_amount=amount,
        settings=settings,
        source=f"partner:{claim.provider}",
        boost_bp=boost_bp,
    )
    events.emit(
        session,
        "partner_task_completed",
        user_id=user.id,
        title=claim.title,
        amount=amount,
        provider=claim.provider,
    )
    return amount


# --- stats (admin) ---------------------------------------------------------------------


async def completed_count(session: AsyncSession, *, user_id: int | None = None) -> int:
    stmt = select(func.count()).select_from(PartnerTaskClaim).where(
        PartnerTaskClaim.status == PartnerTaskStatus.DONE.value
    )
    if user_id is not None:
        stmt = stmt.where(PartnerTaskClaim.user_id == user_id)
    return int((await session.execute(stmt)).scalar_one())


async def payout_by_provider(session: AsyncSession) -> list[tuple[str, int, int]]:
    """``(provider, completions, stars paid)`` — what each partner produced."""
    rows = await session.execute(
        select(
            PartnerTaskClaim.provider,
            func.count(),
            func.coalesce(func.sum(PartnerTaskClaim.reward), 0),
        )
        .where(PartnerTaskClaim.status == PartnerTaskStatus.DONE.value)
        .group_by(PartnerTaskClaim.provider)
        .order_by(func.count().desc())
    )
    return [(str(name), int(count), int(total)) for name, count, total in rows.all()]
