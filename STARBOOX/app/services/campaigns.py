"""Paid promotion: users buy placement inside the bot with Telegram Stars.

Model is CPA — the advertiser pays for a fixed number of *completions*:

    price XTR = target_count × reward(kind) × XTR_PER_REWARD × (100 + fee%) / 100

Lifecycle::

    draft → awaiting_payment → (moderation) → active → done
                                     ↓
                                  rejected  (Stars refunded by an admin)

``active`` campaigns are published in «Задания» next to the partner offers. Each
completion credits the performer, increments ``done_count`` (guarded by a
conditional UPDATE so the budget can never be oversold) and finishes the campaign
when the target is reached.

Verification per kind:

* ``channel`` / ``bot`` — ``getChatMember`` on ``check_chat`` (the bot must be an
  admin there; for a bot target the owner adds our bot as admin of the log channel
  — when that is impossible the campaign falls back to the honour-based flow);
* ``post`` / ``custom`` — the user opens the link and confirms (honour based,
  rate-limited by the global action cooldown).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    OPEN_CAMPAIGN_STATUSES,
    PREMIUM_CAMPAIGN_KINDS,
    Campaign,
    CampaignClaim,
    CampaignKind,
    CampaignStatus,
    LedgerKind,
    User,
)
from app.op.manual import parse_channel_entry, validate_channel_entry
from app.services import events, ledger, referrals
from app.services.antifraud import bump_activity, ensure_action_cooldown, ensure_not_banned
from app.services.boosts import active_multiplier_bp
from app.services.errors import AlreadyClaimed, EconomyError, NotFound, ValidationError
from app.services.levels import XP_TASK, add_xp, apply_multipliers, info_for_xp

MembershipChecker = Callable[[str], Awaitable[bool | None]]

MIN_TITLE_LEN = 3
MAX_TITLE_LEN = 64
MAX_DESCRIPTION_LEN = 300
_URL_PREFIXES = ("https://t.me/", "http://t.me/", "tg://")
CHECKED_KINDS = frozenset({CampaignKind.CHANNEL.value, CampaignKind.BOT.value})


@dataclass(slots=True)
class CampaignQuote:
    kind: str
    target_count: int
    reward: int
    price_xtr: int
    premium_only: bool = False

    @property
    def payout(self) -> int:
        return self.reward * self.target_count


def quote(
    settings: Settings, *, kind: str, target_count: int, premium_only: bool = False
) -> CampaignQuote:
    validate_kind(kind)
    validate_target(settings, target_count)
    premium_only = premium_only or kind in PREMIUM_CAMPAIGN_KINDS
    return CampaignQuote(
        kind=kind,
        target_count=target_count,
        reward=settings.campaign_reward(kind),
        price_xtr=settings.campaign_price(kind, target_count, premium_only=premium_only),
        premium_only=premium_only,
    )


def validate_kind(kind: str) -> None:
    if kind not in {item.value for item in CampaignKind}:
        raise ValidationError("Неизвестный тип продвижения")


def validate_target(settings: Settings, target_count: int) -> None:
    low = max(settings.campaign_min_target, 1)
    high = max(settings.campaign_max_target, low)
    if not low <= target_count <= high:
        raise ValidationError(f"Количество выполнений должно быть от {low} до {high}")


def normalize_title(raw: str) -> str:
    title = " ".join(raw.split())[:MAX_TITLE_LEN].strip()
    if len(title) < MIN_TITLE_LEN:
        raise ValidationError(f"Название должно быть от {MIN_TITLE_LEN} символов")
    return title


def normalize_description(raw: str) -> str:
    text = raw.strip()
    if text in {"-", "—", "нет", "skip"}:
        return ""
    return text[:MAX_DESCRIPTION_LEN]


def parse_target(kind: str, raw: str) -> tuple[str, str | None]:
    """Return ``(url, check_chat)`` for the advertiser's input."""
    value = raw.strip()
    if not value:
        raise ValidationError("Укажите ссылку")

    if kind in {CampaignKind.CHANNEL.value, CampaignKind.CHANNEL_BOOST.value}:
        # Same notation as the OP channel list: @name or -100…|https://t.me/+invite
        problem = validate_channel_entry(value)
        if problem:
            raise ValidationError(f"Канал: {problem}")
        entry = parse_channel_entry(value)
        return entry.url, str(entry.chat)

    if kind == CampaignKind.BOT.value:
        if value.startswith("@"):
            value = f"https://t.me/{value[1:]}"
        if not value.startswith(_URL_PREFIXES):
            raise ValidationError("Ссылка на бота должна быть вида https://t.me/имя_бота")
        return value, None

    if not value.startswith(("https://", "http://", "tg://")):
        raise ValidationError("Ссылка должна начинаться с https://")
    return value, None


async def active_for_owner(session: AsyncSession, owner_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(Campaign)
        .where(Campaign.owner_id == owner_id, Campaign.status.in_(OPEN_CAMPAIGN_STATUSES))
    )
    return int((await session.execute(stmt)).scalar_one())


async def create_draft(
    session: AsyncSession,
    *,
    owner: User,
    kind: str,
    title: str,
    description: str,
    target_raw: str,
    target_count: int,
    settings: Settings,
    premium_only: bool = False,
) -> Campaign:
    if not settings.promo_campaigns_enabled:
        raise EconomyError("Продвижение временно отключено")
    ensure_not_banned(owner)
    validate_kind(kind)
    validate_target(settings, target_count)

    limit = max(settings.campaign_max_active_per_user, 1)
    if await active_for_owner(session, owner.id) >= limit:
        raise ValidationError(f"У вас уже {limit} активных кампаний. Дождитесь их завершения.")

    url, check_chat = parse_target(kind, target_raw)
    premium_only = premium_only or kind in PREMIUM_CAMPAIGN_KINDS
    price = settings.campaign_price(kind, target_count, premium_only=premium_only)
    verification_mode = {
        CampaignKind.CHANNEL.value: "telegram",
        CampaignKind.BOT.value: "telegram",
        CampaignKind.POLL.value: "poll_or_moderation",
        CampaignKind.REACTION.value: "moderation",
        CampaignKind.PREMIUM_REACTION.value: "moderation",
        CampaignKind.CHANNEL_BOOST.value: "telegram",
    }.get(kind, "manual")
    campaign = Campaign(
        owner_id=owner.id,
        kind=kind,
        title=normalize_title(title),
        description=normalize_description(description),
        url=url[:512],
        check_chat=check_chat,
        reward=settings.campaign_reward(kind),
        target_count=target_count,
        xtr_price=price,
        premium_only=premium_only,
        verification_mode=verification_mode,
        status=CampaignStatus.AWAITING_PAYMENT.value,
    )
    session.add(campaign)
    await session.flush()
    return campaign


async def activate_paid(
    session: AsyncSession,
    *,
    campaign: Campaign,
    telegram_charge_id: str,
    settings: Settings,
) -> bool:
    """Mark a campaign as paid. Idempotent per ``telegram_charge_id``.

    Returns False when this charge was already applied (Telegram redelivery).
    """
    if campaign.telegram_charge_id == telegram_charge_id:
        return False
    if campaign.telegram_charge_id:
        raise ValidationError("Кампания уже оплачена")
    campaign.telegram_charge_id = telegram_charge_id
    campaign.status = (
        CampaignStatus.MODERATION.value
        if settings.campaign_moderation
        else CampaignStatus.ACTIVE.value
    )
    await session.flush()
    if campaign.status == CampaignStatus.MODERATION.value:
        events.emit(
            session,
            "campaign_moderation",
            campaign_id=campaign.id,
            owner_id=campaign.owner_id,
        )
    else:
        events.emit(
            session,
            "campaign_status",
            campaign_id=campaign.id,
            owner_id=campaign.owner_id,
            status=campaign.status,
        )
    return True


async def get_campaign(session: AsyncSession, campaign_id: int) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise NotFound("Кампания не найдена")
    return campaign


async def list_for_owner(session: AsyncSession, owner_id: int, *, limit: int = 20) -> list[Campaign]:
    rows = await session.execute(
        select(Campaign).where(Campaign.owner_id == owner_id).order_by(Campaign.id.desc()).limit(limit)
    )
    return list(rows.scalars().all())


async def list_available(
    session: AsyncSession,
    *,
    user: User,
    settings: Settings,
    limit: int = 10,
) -> list[Campaign]:
    """Active campaigns with budget left, excluding the user's own and done ones."""
    if not settings.promo_campaigns_enabled:
        return []
    done = select(CampaignClaim.campaign_id).where(CampaignClaim.user_id == user.id)
    rows = await session.execute(
        select(Campaign)
        .where(
            Campaign.status == CampaignStatus.ACTIVE.value,
            Campaign.owner_id != user.id,
            Campaign.done_count < Campaign.target_count,
            Campaign.id.not_in(done),
            # Telegram Premium campaigns must never leak into the regular feed.
            (Campaign.premium_only.is_(False) | (Campaign.premium_only.is_(True) & (user.is_premium is True))),
        )
        .order_by(Campaign.reward.desc(), Campaign.id)
        .limit(limit)
    )
    return list(rows.scalars().all())


async def claimed_ids(session: AsyncSession, user_id: int) -> set[int]:
    rows = await session.execute(select(CampaignClaim.campaign_id).where(CampaignClaim.user_id == user_id))
    return {int(value) for value in rows.scalars().all()}


async def moderation_queue(session: AsyncSession, *, limit: int = 20) -> list[Campaign]:
    rows = await session.execute(
        select(Campaign)
        .where(Campaign.status == CampaignStatus.MODERATION.value)
        .order_by(Campaign.id)
        .limit(limit)
    )
    return list(rows.scalars().all())


async def count_moderation(session: AsyncSession) -> int:
    stmt = (
        select(func.count())
        .select_from(Campaign)
        .where(Campaign.status == CampaignStatus.MODERATION.value)
    )
    return int((await session.execute(stmt)).scalar_one())


# --- performing a campaign --------------------------------------------------------------


async def complete(
    session: AsyncSession,
    *,
    user: User,
    campaign: Campaign,
    settings: Settings,
    membership_checker: MembershipChecker | None = None,
) -> int:
    """Credit the performer for one completion. Returns the amount paid."""
    ensure_not_banned(user)
    if campaign.owner_id == user.id:
        raise EconomyError("Нельзя выполнять собственное задание")
    if campaign.status != CampaignStatus.ACTIVE.value:
        raise EconomyError("Задание больше недоступно")
    if campaign.premium_only and not user.is_premium:
        raise EconomyError("Это задание доступно только пользователям Telegram Premium")
    if campaign.budget_left <= 0:
        raise EconomyError("Лимит выполнений по заданию исчерпан")
    ensure_action_cooldown(user, settings)

    if campaign.kind in CHECKED_KINDS and campaign.check_chat:
        if membership_checker is None:
            raise EconomyError("Проверка подписки временно недоступна")
        verdict = await membership_checker(campaign.check_chat)
        if verdict is False:
            raise EconomyError("Подписка ещё не найдена. Выполните задание и нажмите «Проверить».")

    claim = CampaignClaim(campaign_id=campaign.id, user_id=user.id, reward=campaign.reward)
    session.add(claim)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AlreadyClaimed("Задание уже выполнено") from exc

    # Conditional increment: two concurrent completions can never exceed the budget.
    result = await session.execute(
        update(Campaign)
        .where(Campaign.id == campaign.id, Campaign.done_count < Campaign.target_count)
        .values(done_count=Campaign.done_count + 1)
        .returning(Campaign.done_count)
    )
    done_count = result.scalar_one_or_none()
    if done_count is None:
        await session.rollback()
        raise EconomyError("Лимит выполнений по заданию исчерпан")
    campaign.done_count = int(done_count)

    level_bp = info_for_xp(user.xp).multiplier_bp
    boost_bp = await active_multiplier_bp(session, user.id)
    amount = apply_multipliers(campaign.reward, level_bp, boost_bp)

    await ledger.credit(
        session,
        user_id=user.id,
        amount=amount,
        kind=LedgerKind.PROMO_TASK,
        reference=f"campaign:{campaign.id}"[:64],
        extra={"campaign_id": campaign.id, "base": campaign.reward, "kind": campaign.kind},
    )
    await add_xp(session, user, XP_TASK)
    await bump_activity(session, user, 1)
    await referrals.activate_if_ready(session, user=user, settings=settings, boost_bp=boost_bp)
    await referrals.share_earning(
        session,
        earner=user,
        base_amount=amount,
        settings=settings,
        source=f"campaign:{campaign.id}",
        boost_bp=boost_bp,
    )

    if campaign.done_count >= campaign.target_count:
        campaign.status = CampaignStatus.DONE.value
        campaign.finished_at = datetime.now(UTC)
        events.emit(
            session,
            "campaign_status",
            campaign_id=campaign.id,
            owner_id=campaign.owner_id,
            status=campaign.status,
        )
    await session.flush()
    return amount


# --- moderation / admin ------------------------------------------------------------------


async def approve(session: AsyncSession, *, campaign: Campaign, admin_id: int) -> Campaign:
    if campaign.status != CampaignStatus.MODERATION.value:
        raise ValidationError("Кампания не на модерации")
    campaign.status = CampaignStatus.ACTIVE.value
    campaign.reviewed_by = admin_id
    campaign.reviewed_at = datetime.now(UTC)
    await session.flush()
    events.emit(
        session,
        "campaign_status",
        campaign_id=campaign.id,
        owner_id=campaign.owner_id,
        status=campaign.status,
    )
    return campaign


async def reject(
    session: AsyncSession,
    *,
    campaign: Campaign,
    admin_id: int,
    reason: str,
) -> Campaign:
    if campaign.status in {CampaignStatus.DONE.value, CampaignStatus.REJECTED.value}:
        raise ValidationError("Кампанию уже нельзя отклонить")
    campaign.status = CampaignStatus.REJECTED.value
    campaign.moderation_note = reason.strip()[:500]
    campaign.reviewed_by = admin_id
    campaign.reviewed_at = datetime.now(UTC)
    campaign.finished_at = datetime.now(UTC)
    await session.flush()
    events.emit(
        session,
        "campaign_status",
        campaign_id=campaign.id,
        owner_id=campaign.owner_id,
        status=campaign.status,
        note=campaign.moderation_note or "",
    )
    return campaign


async def set_paused(session: AsyncSession, *, campaign: Campaign, paused: bool) -> Campaign:
    if paused and campaign.status != CampaignStatus.ACTIVE.value:
        raise ValidationError("Пауза доступна только активной кампании")
    if not paused and campaign.status != CampaignStatus.PAUSED.value:
        raise ValidationError("Кампания не на паузе")
    campaign.status = CampaignStatus.PAUSED.value if paused else CampaignStatus.ACTIVE.value
    await session.flush()
    return campaign


async def mark_refunded(session: AsyncSession, *, campaign: Campaign) -> None:
    campaign.refunded_at = datetime.now(UTC)
    if campaign.status != CampaignStatus.DONE.value:
        campaign.status = CampaignStatus.REJECTED.value
        campaign.finished_at = datetime.now(UTC)
    await session.flush()


async def get_by_charge(session: AsyncSession, telegram_charge_id: str) -> Campaign | None:
    rows = await session.execute(select(Campaign).where(Campaign.telegram_charge_id == telegram_charge_id))
    return rows.scalar_one_or_none()


async def stats(session: AsyncSession) -> dict[str, int]:
    total = int((await session.execute(select(func.count()).select_from(Campaign))).scalar_one())
    active = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Campaign)
                .where(Campaign.status == CampaignStatus.ACTIVE.value)
            )
        ).scalar_one()
    )
    revenue = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(Campaign.xtr_price), 0)).where(
                    Campaign.telegram_charge_id.is_not(None), Campaign.refunded_at.is_(None)
                )
            )
        ).scalar_one()
    )
    completions = int(
        (await session.execute(select(func.count()).select_from(CampaignClaim))).scalar_one()
    )
    return {
        "total": total,
        "active": active,
        "moderation": await count_moderation(session),
        "revenue_xtr": revenue,
        "completions": completions,
    }
