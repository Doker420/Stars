"""Advertiser side of the paid promotion: a 5-step wizard ending in a Stars invoice."""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LabeledPrice, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards, texts
from app.bot.handlers.states import UserFSM
from app.bot.utils import parse_id, safe_answer, safe_edit
from app.config import Settings
from app.db.models import Campaign, CampaignKind, CampaignStatus, User
from app.services import campaigns as campaign_service
from app.services.errors import EconomyError

router = Router(name="promote")

CAMPAIGN_PAYLOAD_PREFIX = "campaign"


def invoice_payload(campaign: Campaign) -> str:
    return f"{CAMPAIGN_PAYLOAD_PREFIX}:{campaign.id}:{campaign.owner_id}"


def parse_campaign_payload(payload: str | None) -> int | None:
    parts = (payload or "").split(":")
    if len(parts) != 3 or parts[0] != CAMPAIGN_PAYLOAD_PREFIX or not parts[1].isdigit():
        return None
    return int(parts[1])


async def _home_view(session: AsyncSession, user: User, settings: Settings):
    items = await campaign_service.list_for_owner(session, user.id)
    active = await campaign_service.active_for_owner(session, user.id)
    limit = max(settings.campaign_max_active_per_user, 1)
    enabled = settings.promo_campaigns_enabled
    return (
        texts.promote_home(active, limit, enabled),
        keyboards.promote_home(bool(items), enabled),
    )


@router.callback_query(F.data == "menu:promote")
async def menu_promote(
    call: CallbackQuery, session: AsyncSession, db_user: User, settings: Settings, state: FSMContext
) -> None:
    await state.clear()
    text, markup = await _home_view(session, db_user, settings)
    await safe_answer(call)
    await safe_edit(call.message, text, markup)


@router.callback_query(F.data == "pr:list")
async def promote_list(call: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    items = await campaign_service.list_for_owner(session, db_user.id)
    await safe_answer(call)
    await safe_edit(call.message, texts.my_campaigns(items), keyboards.my_campaigns(items))


@router.callback_query(F.data.startswith("pr:view:"))
async def promote_view(call: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    campaign = await session.get(Campaign, parse_id(call.data))
    if campaign is None or campaign.owner_id != db_user.id:
        await safe_answer(call, "Кампания не найдена", alert=True)
        return
    await safe_answer(call)
    await safe_edit(call.message, texts.campaign_card(campaign), keyboards.campaign_card(campaign))


@router.callback_query(F.data.regexp(r"^pr:(pause|resume):\d+$"))
async def promote_toggle(call: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    paused = ":pause:" in (call.data or "")
    campaign = await session.get(Campaign, parse_id(call.data))
    if campaign is None or campaign.owner_id != db_user.id:
        await safe_answer(call, "Кампания не найдена", alert=True)
        return
    try:
        await campaign_service.set_paused(session, campaign=campaign, paused=paused)
    except EconomyError as exc:
        await safe_answer(call, exc.message, alert=True)
        return
    await safe_answer(call, "На паузе" if paused else "Запущена")
    await safe_edit(call.message, texts.campaign_card(campaign), keyboards.campaign_card(campaign))


# --- wizard ---------------------------------------------------------------------------


@router.callback_query(F.data == "pr:new")
async def promote_new(
    call: CallbackQuery, session: AsyncSession, db_user: User, settings: Settings, state: FSMContext
) -> None:
    if not settings.promo_campaigns_enabled:
        await safe_answer(call, "Продвижение временно отключено", alert=True)
        return
    limit = max(settings.campaign_max_active_per_user, 1)
    if await campaign_service.active_for_owner(session, db_user.id) >= limit:
        await safe_answer(call, f"У вас уже {limit} активных кампаний", alert=True)
        return
    await state.clear()
    await state.set_state(UserFSM.campaign_kind)
    await safe_answer(call)
    await safe_edit(call.message, texts.promote_kind_prompt(), keyboards.promote_kinds())


@router.callback_query(F.data.startswith("pr:kind:"))
async def promote_kind(call: CallbackQuery, state: FSMContext) -> None:
    kind = (call.data or "").split(":")[-1]
    if kind not in {item.value for item in CampaignKind}:
        await safe_answer(call, "Неизвестный формат", alert=True)
        return
    await state.set_state(UserFSM.campaign_title)
    await state.update_data(kind=kind)
    await safe_answer(call)
    await safe_edit(
        call.message, texts.promote_title_prompt(kind), keyboards.cancel_only("menu:promote")
    )


@router.message(StateFilter(UserFSM.campaign_title), F.text)
async def promote_title(message: Message, state: FSMContext) -> None:
    try:
        title = campaign_service.normalize_title(message.text or "")
    except EconomyError as exc:
        await message.answer(f"⚠️ {exc.message}")
        return
    data = await state.get_data()
    await state.update_data(title=title)
    await state.set_state(UserFSM.campaign_target)
    await message.answer(
        texts.promote_target_prompt(data["kind"]), reply_markup=keyboards.cancel_only("menu:promote")
    )


@router.message(StateFilter(UserFSM.campaign_target), F.text)
async def promote_target(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        url, check_chat = campaign_service.parse_target(data["kind"], message.text or "")
    except EconomyError as exc:
        await message.answer(f"⚠️ {exc.message}")
        return
    await state.update_data(url=url, check_chat=check_chat, target_raw=(message.text or "").strip())
    await state.set_state(UserFSM.campaign_description)
    await message.answer(
        texts.promote_description_prompt(), reply_markup=keyboards.cancel_only("menu:promote")
    )


@router.message(StateFilter(UserFSM.campaign_description), F.text)
async def promote_description(message: Message, state: FSMContext, settings: Settings) -> None:
    data = await state.get_data()
    await state.update_data(description=campaign_service.normalize_description(message.text or ""))
    await state.set_state(UserFSM.campaign_count)
    await message.answer(
        texts.promote_count_prompt(data["kind"], settings),
        reply_markup=keyboards.cancel_only("menu:promote"),
    )


@router.message(StateFilter(UserFSM.campaign_count), F.text)
async def promote_count(message: Message, state: FSMContext, settings: Settings) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ Нужно целое число выполнений.")
        return
    data = await state.get_data()
    try:
        quote = campaign_service.quote(settings, kind=data["kind"], target_count=int(raw))
    except EconomyError as exc:
        await message.answer(f"⚠️ {exc.message}")
        return
    await state.update_data(target_count=quote.target_count, price=quote.price_xtr)
    await state.set_state(UserFSM.campaign_confirm)
    await message.answer(
        texts.promote_confirm(
            data["kind"],
            data["title"],
            data["url"],
            data.get("description", ""),
            quote,
            settings.campaign_moderation,
        ),
        reply_markup=keyboards.promote_confirm(),
    )


@router.callback_query(StateFilter(UserFSM.campaign_confirm), F.data == "pr:pay")
async def promote_pay(
    call: CallbackQuery, session: AsyncSession, db_user: User, settings: Settings, state: FSMContext
) -> None:
    data = await state.get_data()
    try:
        campaign = await campaign_service.create_draft(
            session,
            owner=db_user,
            kind=data["kind"],
            title=data["title"],
            description=data.get("description", ""),
            target_raw=data.get("target_raw") or data["url"],
            target_count=int(data["target_count"]),
            settings=settings,
        )
    except EconomyError as exc:
        await safe_answer(call, exc.message, alert=True)
        return
    await state.clear()
    await safe_answer(call)
    await safe_edit(call.message, texts.campaign_payment_prompt(campaign), None)
    await _send_invoice(call.message, campaign)


@router.callback_query(F.data.startswith("pr:payid:"))
async def promote_pay_existing(call: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    campaign = await session.get(Campaign, parse_id(call.data))
    if campaign is None or campaign.owner_id != db_user.id:
        await safe_answer(call, "Кампания не найдена", alert=True)
        return
    if campaign.status != CampaignStatus.AWAITING_PAYMENT.value:
        await safe_answer(call, "Кампания уже оплачена", alert=True)
        return
    await safe_answer(call)
    await _send_invoice(call.message, campaign)


async def _send_invoice(message: Message | None, campaign: Campaign) -> None:
    if message is None:
        return
    await message.answer_invoice(
        title=f"Продвижение #{campaign.id}"[:32],
        description=(
            f"{campaign.title} — {campaign.target_count} выполнений "
            f"по {campaign.reward} ⭐"
        )[:255],
        payload=invoice_payload(campaign),
        currency="XTR",
        prices=[LabeledPrice(label=f"Кампания #{campaign.id}"[:32], amount=campaign.xtr_price)],
    )
