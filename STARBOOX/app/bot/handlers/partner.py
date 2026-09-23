"""Rewarded tasks from the monetization providers and from paid campaigns.

Partner offers are fetched live from the provider APIs on every screen, so there is
no stable database id to put in a callback. The list is therefore cached in the
user's FSM state and referenced by position; a stale index just sends the user back
to a freshly fetched list instead of paying for the wrong offer.
"""

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards, texts
from app.bot.utils import parse_id, safe_answer, safe_edit
from app.config import Settings
from app.db.models import Campaign, PartnerTaskClaim, User
from app.op.base import PartnerOffer
from app.op.gate import OpGate
from app.op.manual import is_member
from app.services import campaigns as campaign_service
from app.services import partner_tasks as partner_service
from app.services.errors import EconomyError
from app.services.partner_tasks import OfferView

router = Router(name="partner")

_STATE_KEY = "partner_offers"


def _chat_id(call: CallbackQuery, user: User) -> int:
    if call.message and call.message.chat:
        return call.message.chat.id
    return user.id


def _dump(views: list[OfferView]) -> list[dict]:
    return [
        {
            "external_id": view.offer.external_id,
            "title": view.offer.title,
            "url": view.offer.url,
            "kind": view.offer.kind,
            "provider": view.offer.provider,
            "description": view.offer.description,
            "meta": view.offer.meta,
            "reward": view.reward,
            "claim_id": view.claim_id,
        }
        for view in views
    ]


def _load(raw: dict) -> OfferView:
    offer = PartnerOffer(
        external_id=str(raw.get("external_id", "")),
        title=str(raw.get("title", "")),
        url=str(raw.get("url", "")),
        kind=str(raw.get("kind", "channel")),
        provider=str(raw.get("provider", "")),
        description=str(raw.get("description", "")),
        meta=dict(raw.get("meta") or {}),
    )
    return OfferView(offer=offer, reward=int(raw.get("reward", 0)), claim_id=raw.get("claim_id"))


# --- partner offers -------------------------------------------------------------------


@router.callback_query(F.data == "menu:partner")
async def menu_partner(
    call: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    settings: Settings,
    state: FSMContext,
    op_gate: OpGate,
    bot: Bot,
) -> None:
    await safe_answer(call)
    views = await partner_service.list_offers(
        session,
        user=db_user,
        gate=op_gate,
        settings=settings,
        bot=bot,
        chat_id=_chat_id(call, db_user),
    )
    await state.update_data(**{_STATE_KEY: _dump(views)})
    await safe_edit(
        call.message,
        texts.partner_tasks_header(views),
        keyboards.partner_tasks_keyboard(views),
    )


async def _view_at(state: FSMContext, index: int) -> OfferView | None:
    data = await state.get_data()
    items = data.get(_STATE_KEY) or []
    if not 0 <= index < len(items):
        return None
    return _load(items[index])


@router.callback_query(F.data.startswith("pt:view:"))
async def partner_view(
    call: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    settings: Settings,
    state: FSMContext,
) -> None:
    index = parse_id(call.data)
    view = await _view_at(state, index)
    if view is None:
        await safe_answer(call, "Список устарел — обновите задания", alert=True)
        return
    try:
        claim = await partner_service.reserve(
            session, user=db_user, offer=view.offer, settings=settings
        )
    except EconomyError as exc:
        await safe_answer(call, exc.message, alert=True)
        return
    view.claim_id = claim.id
    data = await state.get_data()
    items = list(data.get(_STATE_KEY) or [])
    if 0 <= index < len(items):
        items[index] = {**items[index], "claim_id": claim.id}
        await state.update_data(**{_STATE_KEY: items})
    await safe_answer(call)
    await safe_edit(
        call.message,
        texts.partner_task_card(view),
        keyboards.partner_task_card(view, index),
    )


@router.callback_query(F.data.startswith("pt:do:"))
async def partner_do(
    call: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    settings: Settings,
    state: FSMContext,
    op_gate: OpGate,
    bot: Bot,
) -> None:
    index = parse_id(call.data)
    view = await _view_at(state, index)
    if view is None or view.claim_id is None:
        await safe_answer(call, "Список устарел — обновите задания", alert=True)
        return
    claim = await session.get(PartnerTaskClaim, view.claim_id)
    if claim is None or claim.user_id != db_user.id:
        await safe_answer(call, "Задание не найдено", alert=True)
        return
    try:
        amount = await partner_service.complete(
            session,
            user=db_user,
            claim=claim,
            gate=op_gate,
            settings=settings,
            bot=bot,
            chat_id=_chat_id(call, db_user),
        )
    except EconomyError as exc:
        await safe_answer(call, exc.message, alert=True)
        return
    await safe_answer(call, f"+{amount} ⭐", alert=True)
    views = await partner_service.list_offers(
        session,
        user=db_user,
        gate=op_gate,
        settings=settings,
        bot=bot,
        chat_id=_chat_id(call, db_user),
    )
    await state.update_data(**{_STATE_KEY: _dump(views)})
    await safe_edit(
        call.message,
        texts.partner_task_done(claim.title, amount),
        keyboards.partner_tasks_keyboard(views),
    )


# --- campaigns from other users -------------------------------------------------------


@router.callback_query(F.data == "menu:promotasks")
async def menu_promo_tasks(
    call: CallbackQuery, session: AsyncSession, db_user: User, settings: Settings
) -> None:
    items = await campaign_service.list_available(session, user=db_user, settings=settings)
    await safe_answer(call)
    await safe_edit(
        call.message, texts.promo_tasks_header(items), keyboards.promo_tasks_keyboard(items)
    )


@router.callback_query(F.data.startswith("ct:view:"))
async def promo_task_view(call: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    campaign = await session.get(Campaign, parse_id(call.data))
    if campaign is None or not campaign.is_visible or campaign.owner_id == db_user.id:
        await safe_answer(call, "Задание недоступно", alert=True)
        return
    await safe_answer(call)
    await safe_edit(call.message, texts.promo_task_card(campaign), keyboards.promo_task_card(campaign))


@router.callback_query(F.data.startswith("ct:do:"))
async def promo_task_do(
    call: CallbackQuery, session: AsyncSession, db_user: User, settings: Settings, bot: Bot
) -> None:
    campaign_id = parse_id(call.data)
    try:
        campaign = await campaign_service.get_campaign(session, campaign_id)
    except EconomyError as exc:
        await safe_answer(call, exc.message, alert=True)
        return

    async def _checker(chat: str) -> bool | None:
        return await is_member(bot, db_user.id, chat)

    try:
        amount = await campaign_service.complete(
            session,
            user=db_user,
            campaign=campaign,
            settings=settings,
            membership_checker=_checker,
        )
    except EconomyError as exc:
        await safe_answer(call, exc.message, alert=True)
        return
    await safe_answer(call, f"+{amount} ⭐", alert=True)
    items = await campaign_service.list_available(session, user=db_user, settings=settings)
    await safe_edit(
        call.message, texts.promo_tasks_header(items), keyboards.promo_tasks_keyboard(items)
    )
