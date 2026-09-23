"""Server-authoritative cases and wheel for the Telegram Mini App."""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CaseOpening, LedgerKind, User, WheelSpin
from app.services import ledger
from app.services.antifraud import ensure_not_banned
from app.services.errors import EconomyError, ValidationError

_rng = random.SystemRandom()

CASES = {
    # One free opening per UTC day. 100 ⭐ is deliberately rare (0.1%): EV ≈ 2.1 ⭐.
    "star_free": {"title": "Звёздный", "icon": "🌟", "free_price": 0,
                  "rewards": (("stars", 1, 650), ("stars", 2, 230), ("stars", 5, 90), ("stars", 10, 29), ("stars", 100, 1))},
    "starter": {"title": "Стартовый", "icon": "🎁", "key_price": 1, "stars_price": 20,
                "rewards": (("stars", 5, 4000), ("stars", 10, 3000), ("stars", 25, 1000), ("spin", 1, 1200), ("key", 1, 799), ("gift", 15, 1))},
    "gold": {"title": "Золотой", "icon": "👑", "key_price": 3, "stars_price": 70,
             "rewards": (("stars", 25, 3500), ("stars", 50, 2800), ("stars", 100, 700), ("spin", 2, 1800), ("key", 2, 1199), ("gift", 50, 1))},
}
# XTR prices intentionally keep the expected payout below revenue. Telegram Stars
# credited inside the bot are a loyalty balance, not withdrawable Telegram XTR.
GAME_PRODUCTS = {
    "keys_5": {"title": "5 ключей", "xtr": 15, "keys": 5},
    "keys_15": {"title": "15 ключей", "xtr": 39, "keys": 15},
    "vip_30": {"title": "STARBOOX VIP на 30 дней", "xtr": 99, "vip_days": 30},
}
WHEEL_REWARDS = (("stars", 2, 30), ("stars", 5, 30), ("stars", 10, 18), ("stars", 25, 4), ("key", 1, 7), ("spin", 1, 11))


def game_product(slug: str) -> dict | None:
    return GAME_PRODUCTS.get(slug)


async def grant_product(user: User, slug: str) -> None:
    product = game_product(slug)
    if product is None:
        raise ValidationError("Товар не найден")
    user.case_keys += int(product.get("keys", 0))
    days = int(product.get("vip_days", 0))
    if days:
        now = datetime.now(UTC)
        base = user.vip_until if user.vip_until and user.vip_until > now else now
        user.vip_until = base + timedelta(days=days)


@dataclass(frozen=True, slots=True)
class GameReward:
    kind: str
    amount: int


def _draw(items: tuple[tuple[str, int, int], ...]) -> GameReward:
    chosen = _rng.choices(items, weights=[item[2] for item in items], k=1)[0]
    return GameReward(chosen[0], chosen[1])


async def _apply_reward(
    session: AsyncSession, user: User, reward: GameReward, reference: str, ledger_kind: LedgerKind
) -> None:
    if reward.kind == "stars":
        await ledger.credit(session, user_id=user.id, amount=reward.amount, kind=ledger_kind, reference=reference)
    elif reward.kind == "spin":
        user.spins += reward.amount
    elif reward.kind == "key":
        user.case_keys += reward.amount
    elif reward.kind == "gift":
        # Telegram gift delivery is processed from the pending opening by an admin.
        # Never pretend it was delivered before Telegram confirms the transfer.
        return
    else:
        raise ValidationError("Неизвестная награда")


async def open_case(session: AsyncSession, *, user: User, case_slug: str, payment: str, request_id: str) -> CaseOpening:
    ensure_not_banned(user)
    if not 8 <= len(request_id) <= 64:
        raise ValidationError("Некорректный идентификатор операции")
    existing = (await session.execute(select(CaseOpening).where(CaseOpening.request_id == request_id))).scalar_one_or_none()
    if existing is not None:
        if existing.user_id != user.id:
            raise ValidationError("Идентификатор операции уже использован")
        return existing
    case = CASES.get(case_slug)
    allowed_payments = {key.removesuffix("_price") for key in case or {} if key.endswith("_price")}
    if case is None or payment not in allowed_payments:
        raise ValidationError("Кейс или способ оплаты не найден")
    price = int(case[f"{payment}_price"])
    if payment == "free":
        today = datetime.now(UTC).date()
        if user.last_free_case_on == today:
            raise EconomyError("Бесплатный Звёздный кейс уже открыт сегодня")
        user.last_free_case_on = today
    elif payment == "key":
        if user.case_keys < price:
            raise EconomyError("Недостаточно ключей")
        user.case_keys -= price
    else:
        if user.balance < price:
            raise EconomyError("Недостаточно Stars на балансе")
        await ledger.debit(session, user_id=user.id, amount=price, kind=LedgerKind.GAME_PURCHASE, reference=f"case:{request_id}")
    reward = _draw(case["rewards"])
    row = CaseOpening(
        user_id=user.id,
        case_slug=case_slug,
        payment_method=payment,
        price=price,
        reward_kind=reward.kind,
        reward_amount=reward.amount,
        fulfillment_status="pending" if reward.kind == "gift" else "credited",
        request_id=request_id,
    )
    session.add(row)
    await _apply_reward(session, user, reward, f"case_reward:{request_id}", LedgerKind.CASE_REWARD)
    await session.flush()
    return row


async def spin_wheel(session: AsyncSession, *, user: User, request_id: str) -> WheelSpin:
    ensure_not_banned(user)
    if not 8 <= len(request_id) <= 64:
        raise ValidationError("Некорректный идентификатор операции")
    existing = (await session.execute(select(WheelSpin).where(WheelSpin.request_id == request_id))).scalar_one_or_none()
    if existing is not None:
        if existing.user_id != user.id:
            raise ValidationError("Идентификатор операции уже использован")
        return existing
    if user.spins < 1:
        raise EconomyError("Нет доступных спинов")
    user.spins -= 1
    reward = _draw(WHEEL_REWARDS)
    row = WheelSpin(user_id=user.id, reward_kind=reward.kind, reward_amount=reward.amount, request_id=request_id)
    session.add(row)
    await _apply_reward(session, user, reward, f"wheel:{request_id}", LedgerKind.WHEEL_REWARD)
    await session.flush()
    return row
