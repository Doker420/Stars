"""Admin: moderation queue for paid promotions and partner-task statistics."""

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.admin import keyboards as kb
from app.bot.admin import texts
from app.bot.admin.states import AdminFSM
from app.bot.utils import parse_id, safe_answer, safe_edit
from app.db.models import Campaign, User
from app.services import audit
from app.services import campaigns as campaign_service
from app.services import partner_tasks as partner_service
from app.services import payments as payment_service
from app.services.errors import EconomyError

router = Router(name="admin.campaigns")


async def _home_view(session: AsyncSession):
    queue = await campaign_service.moderation_queue(session)
    stats = await campaign_service.stats(session)
    payouts = await partner_service.payout_by_provider(session)
    return texts.campaigns_home(queue, stats, payouts), kb.campaigns_home(queue)


async def _card_view(session: AsyncSession, campaign: Campaign):
    owner = await session.get(User, campaign.owner_id)
    return texts.campaign_admin_card(campaign, owner), kb.campaign_admin_card(campaign)


@router.callback_query(F.data == "admin:camp")
async def campaigns_home(call: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    text, markup = await _home_view(session)
    await safe_answer(call)
    await safe_edit(call.message, text, markup)


@router.callback_query(F.data.regexp(r"^admin:camp:(\d+)$"))
async def campaign_card(call: CallbackQuery, session: AsyncSession) -> None:
    campaign = await session.get(Campaign, parse_id(call.data))
    if campaign is None:
        await safe_answer(call, "Кампания не найдена", alert=True)
        return
    text, markup = await _card_view(session, campaign)
    await safe_answer(call)
    await safe_edit(call.message, text, markup)


@router.callback_query(F.data.regexp(r"^admin:camp:(\d+):ok$"))
async def campaign_approve(call: CallbackQuery, session: AsyncSession) -> None:
    campaign = await session.get(Campaign, parse_id(call.data, -2))
    if campaign is None:
        await safe_answer(call, "Кампания не найдена", alert=True)
        return
    try:
        await campaign_service.approve(session, campaign=campaign, admin_id=call.from_user.id)
    except EconomyError as exc:
        await safe_answer(call, exc.message, alert=True)
        return
    await audit.log_action(
        session,
        admin_id=call.from_user.id,
        action="campaign.approve",
        target_type="campaign",
        target_id=campaign.id,
    )
    await safe_answer(call, "Кампания запущена")
    text, markup = await _card_view(session, campaign)
    await safe_edit(call.message, text, markup)


@router.callback_query(F.data.regexp(r"^admin:camp:(\d+):no$"))
async def campaign_reject_prompt(call: CallbackQuery, state: FSMContext) -> None:
    campaign_id = parse_id(call.data, -2)
    await state.set_state(AdminFSM.campaign_reject_reason)
    await state.update_data(campaign_id=campaign_id)
    await safe_answer(call)
    await safe_edit(
        call.message, texts.campaign_reject_prompt(campaign_id), kb.cancel_to(f"admin:camp:{campaign_id}")
    )


@router.message(StateFilter(AdminFSM.campaign_reject_reason), F.text)
async def campaign_reject(
    message: Message, session: AsyncSession, state: FSMContext, bot: Bot
) -> None:
    data = await state.get_data()
    campaign = await session.get(Campaign, int(data.get("campaign_id", 0)))
    if campaign is None:
        await state.clear()
        await message.answer("Кампания не найдена.")
        return
    reason = (message.text or "").strip()
    try:
        await campaign_service.reject(
            session, campaign=campaign, admin_id=message.from_user.id, reason=reason
        )
    except EconomyError as exc:
        await message.answer(f"⚠️ {exc.message}")
        return
    await state.clear()

    refunded = await _refund_campaign(session, campaign, bot, admin_id=message.from_user.id)
    await audit.log_action(
        session,
        admin_id=message.from_user.id,
        action="campaign.reject",
        target_type="campaign",
        target_id=campaign.id,
        reason=reason[:200],
        refunded=refunded,
    )
    _, markup = await _card_view(session, campaign)
    await message.answer(texts.campaign_rejected(campaign, refunded), reply_markup=markup)


@router.callback_query(F.data.regexp(r"^admin:camp:(\d+):refund$"))
async def campaign_refund(call: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    campaign = await session.get(Campaign, parse_id(call.data, -2))
    if campaign is None:
        await safe_answer(call, "Кампания не найдена", alert=True)
        return
    refunded = await _refund_campaign(session, campaign, bot, admin_id=call.from_user.id)
    await audit.log_action(
        session,
        admin_id=call.from_user.id,
        action="campaign.refund",
        target_type="campaign",
        target_id=campaign.id,
        refunded=refunded,
    )
    await safe_answer(call, "Возврат отправлен" if refunded else "Возврат не прошёл", alert=True)
    text, markup = await _card_view(session, campaign)
    await safe_edit(call.message, text, markup)


async def _refund_campaign(
    session: AsyncSession, campaign: Campaign, bot: Bot, *, admin_id: int
) -> bool:
    """Refund the advertiser's Stars. Best-effort: a Telegram refusal is reported back."""
    if not campaign.telegram_charge_id or campaign.refunded_at is not None:
        return False
    payment = await payment_service.get_by_charge(session, campaign.telegram_charge_id)
    if payment is None:
        return False

    async def _call(user_id: int, charge_id: str) -> bool:
        try:
            return bool(await bot.refund_star_payment(user_id=user_id, telegram_payment_charge_id=charge_id))
        except Exception:
            return False

    try:
        await payment_service.refund(session, payment=payment, admin_id=admin_id, refund_call=_call)
    except EconomyError:
        return False
    await campaign_service.mark_refunded(session, campaign=campaign)
    return True
