"""Server-authoritative cases and wheel for the Telegram Mini App."""
from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CaseOpening, LedgerKind, User, WheelSpin
from app.services import ledger
from app.services.antifraud import ensure_not_banned
from app.services.errors import EconomyError, ValidationError

_rng = random.SystemRandom()

CASES = {
    "starter": {"title": "Стартовый", "icon": "🎁", "key_price": 1, "stars_price": 20,
                "rewards": (("stars", 5, 40), ("stars", 10, 30), ("stars", 25, 10), ("spin", 1, 12), ("key", 1, 8))},
    "gold": {"title": "Золотой", "icon": "👑", "key_price": 3, "stars_price": 70,
             "rewards": (("stars", 25, 35), ("stars", 50, 28), ("stars", 100, 7), ("spin", 2, 18), ("key", 2, 12))},
}
WHEEL_REWARDS = (("stars", 2, 28), ("stars", 5, 28), ("stars", 10, 18), ("stars", 25, 5), ("key", 1, 8), ("spin", 1, 13))


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
    if case is None or payment not in {"key", "stars"}:
        raise ValidationError("Кейс или способ оплаты не найден")
    price = int(case[f"{payment}_price"])
    if payment == "key":
        if user.case_keys < price:
            raise EconomyError("Недостаточно ключей")
        user.case_keys -= price
    else:
        if user.balance < price:
            raise EconomyError("Недостаточно Stars на балансе")
        await ledger.debit(session, user_id=user.id, amount=price, kind=LedgerKind.GAME_PURCHASE, reference=f"case:{request_id}")
    reward = _draw(case["rewards"])
    row = CaseOpening(user_id=user.id, case_slug=case_slug, payment_method=payment, price=price,
                      reward_kind=reward.kind, reward_amount=reward.amount, request_id=request_id)
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
