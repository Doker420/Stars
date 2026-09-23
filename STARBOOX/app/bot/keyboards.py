"""User keyboards."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from app.bot.utils import PAGE_SIZE, button, markup, pager, url_button
from app.db.models import (
    CAMPAIGN_KIND_EMOJI,
    CAMPAIGN_KIND_LABELS,
    BoostProduct,
    Campaign,
    CampaignKind,
    CampaignStatus,
    Task,
    TaskKind,
    Withdrawal,
    WithdrawalStatus,
)
from app.op.base import Sponsor
from app.op.manual import parse_channel_entry
from app.services.partner_tasks import OfferView
from app.services.tasks import task_target


def device_button(url: str) -> InlineKeyboardButton:
    """Opens the device-verification Mini App (private chats only)."""
    return InlineKeyboardButton(text="🛡 Подтвердить устройство", web_app=WebAppInfo(url=url))


def main_menu(
    is_admin: bool = False,
    device_url: str | None = None,
    partner: int = 0,
) -> InlineKeyboardMarkup:
    rows = []
    if device_url:
        rows.append([device_button(device_url)])
    # Partner offers are the monetized inventory: they sit above everything else
    # on purpose, in a full-width row so the tap target is largest. The count comes
    # from the last real fetch, so the badge costs no extra provider call.
    partner_label = f"🤝 Задания партнёров ({partner})" if partner else "🤝 Задания партнёров"
    rows += [
        [button(partner_label, "menu:partner")],
        [button("👤 Профиль", "menu:profile"), button("👥 Рефералы", "menu:refs")],
        [button("🎁 Ежедневка", "menu:daily"), button("📋 Задания", "menu:tasks")],
        [button("📣 Продвижение", "menu:promote"), button("🚀 Бусты", "menu:boosts")],
        [button("🏆 Топ", "menu:top:refs"), button("🎟 Промокод", "menu:promo")],
        [button("💸 Вывод", "menu:withdraw"), button("❓ Помощь", "menu:help")],
    ]
    if is_admin:
        rows.append([button("🛠 Админка", "admin:home")])
    return markup(*rows)


def back_home(*extra_rows: list) -> InlineKeyboardMarkup:
    return markup(*extra_rows, [button("🏠 В меню", "menu:home")])


def profile_menu(digest_on: bool = True) -> InlineKeyboardMarkup:
    toggle = "🔔 Напоминания: вкл" if digest_on else "🔕 Напоминания: выкл"
    return markup(
        [button("📜 История", "menu:history:0"), button("📄 Мои заявки", "wd:list")],
        [button(toggle, "menu:digest:toggle")],
        [button("🏠 В меню", "menu:home")],
    )


def digest_keyboard(tasks: int, campaigns: int, partner: int = 0) -> InlineKeyboardMarkup:
    """Reminder push: straight into whatever the user actually has waiting.

    Partner offers lead — they are the paid inventory.
    """
    rows = []
    if partner:
        rows.append([button(f"🤝 Задания партнёров ({partner})", "menu:partner")])
    else:
        rows.append([button("🤝 Задания партнёров", "menu:partner")])
    if tasks:
        rows.append([button(f"📋 Задания ({tasks})", "menu:tasks")])
    if campaigns:
        rows.append([button(f"📣 Задания пользователей ({campaigns})", "menu:promotasks")])
    rows.append([button("🔕 Не напоминать", "menu:digest:off")])
    return markup(*rows)


def history_menu(page: int, total: int) -> InlineKeyboardMarkup:
    return markup(pager("menu:history", page, total, PAGE_SIZE), [button("👤 Профиль", "menu:profile")])


def referrals_menu(link: str, share_text: str) -> InlineKeyboardMarkup:
    share_url = f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(share_text, safe='')}"
    return markup(
        [url_button("📤 Поделиться ссылкой", share_url)],
        [button("🏆 Топ рефереров", "menu:top:refs"), button("🏠 В меню", "menu:home")],
    )


def daily_menu(claimed: bool) -> InlineKeyboardMarkup:
    rows = []
    if not claimed:
        rows.append([button("🎁 Забрать награду", "daily:claim")])
    rows.append([button("📋 Задания", "menu:tasks"), button("🏠 В меню", "menu:home")])
    return markup(*rows)


def op_keyboard(sponsors: Sequence[Sponsor]) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for sponsor in sponsors:
        title = (sponsor.title or "Спонсор")[:32]
        row.append(url_button(f"➕ {title}", sponsor.url))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([button("✅ Я подписался", "op:verify")])
    return markup(*rows)


def op_intro_keyboard(sponsors: Sequence[Sponsor]) -> InlineKeyboardMarkup:
    """Soft-onboarding block: sponsors plus a way straight into the bot."""
    rows = []
    for sponsor in sponsors:
        rows.append([url_button(f"➕ {(sponsor.title or 'Спонсор')[:32]}", sponsor.url)])
    rows.append([button("🚀 Начать пользоваться", "menu:home")])
    return markup(*rows)


def tasks_keyboard(
    tasks: Sequence[Task],
    done: set[int],
    *,
    partner: int = 0,
    promo: int = 0,
) -> InlineKeyboardMarkup:
    rows = []
    if partner:
        rows.append([button(f"🤝 Задания партнёров ({partner})", "menu:partner")])
    if promo:
        rows.append([button(f"📣 Задания пользователей ({promo})", "menu:promotasks")])
    for task in tasks:
        mark = "✅ " if task.id in done else ""
        rows.append([button(f"{mark}{task.title} · +{task.reward}⭐", f"task:view:{task.id}")])
    rows.append([button("🏠 В меню", "menu:home")])
    return markup(*rows)


# --- partner tasks --------------------------------------------------------------------


def partner_tasks_keyboard(views: Sequence[OfferView]) -> InlineKeyboardMarkup:
    rows = []
    for index, view in enumerate(views):
        title = view.offer.title[:28]
        rows.append([button(f"{view.emoji} {title} · +{view.reward}⭐", f"pt:view:{index}")])
    rows.append([button("🔄 Обновить", "menu:partner")])
    rows.append([button("📋 Все задания", "menu:tasks"), button("🏠 В меню", "menu:home")])
    return markup(*rows)


def partner_task_card(view: OfferView, index: int) -> InlineKeyboardMarkup:
    label = {
        "channel": "📢 Подписаться",
        "bot": "🤖 Запустить бота",
        "boost": "⚡ Дать буст",
        "folder": "📂 Добавить папку",
    }.get(view.offer.kind, "🔗 Перейти")
    return markup(
        [url_button(label, view.offer.url)],
        [button("✅ Проверить", f"pt:do:{index}")],
        [button("‹ К заданиям", "menu:partner"), button("🏠 В меню", "menu:home")],
    )


# --- paid promotion: performer --------------------------------------------------------


def promo_tasks_keyboard(items: Sequence[Campaign]) -> InlineKeyboardMarkup:
    rows = []
    for campaign in items:
        emoji = CAMPAIGN_KIND_EMOJI.get(campaign.kind, "🔗")
        rows.append(
            [button(f"{emoji} {campaign.title[:28]} · +{campaign.reward}⭐", f"ct:view:{campaign.id}")]
        )
    rows.append([button("🔄 Обновить", "menu:promotasks")])
    rows.append([button("📋 Все задания", "menu:tasks"), button("🏠 В меню", "menu:home")])
    return markup(*rows)


def promo_task_card(campaign: Campaign) -> InlineKeyboardMarkup:
    label = {
        CampaignKind.CHANNEL.value: "📢 Открыть канал",
        CampaignKind.BOT.value: "🤖 Запустить бота",
        CampaignKind.POST.value: "📝 Открыть пост",
        CampaignKind.REACTION.value: "👍 Поставить реакцию",
        CampaignKind.POLL.value: "📊 Проголосовать",
        CampaignKind.COMMENT.value: "💬 Оставить комментарий",
        CampaignKind.STORY.value: "👁 Посмотреть Story",
        CampaignKind.PREMIUM_REACTION.value: "💎 Premium-реакция",
        CampaignKind.CHANNEL_BOOST.value: "🚀 Бустить канал",
    }.get(campaign.kind, "🔗 Перейти")
    return markup(
        [url_button(label, campaign.url)],
        [button("✅ Проверить", f"ct:do:{campaign.id}")],
        [button("‹ К заданиям", "menu:promotasks"), button("🏠 В меню", "menu:home")],
    )


# --- paid promotion: advertiser -------------------------------------------------------


def promote_home(has_campaigns: bool, enabled: bool) -> InlineKeyboardMarkup:
    rows = []
    if enabled:
        rows.append([button("➕ Разместить задание", "pr:new")])
    if has_campaigns:
        rows.append([button("📋 Мои кампании", "pr:list")])
    rows.append([button("🏠 В меню", "menu:home")])
    return markup(*rows)


def promote_kinds() -> InlineKeyboardMarkup:
    rows = [
        [
            button(
                f"{CAMPAIGN_KIND_EMOJI[kind.value]} {CAMPAIGN_KIND_LABELS[kind.value]}",
                f"pr:kind:{kind.value}",
            )
        ]
        for kind in CampaignKind
    ]
    rows.append([button("Отмена", "menu:promote")])
    return markup(*rows)


def promote_audience() -> InlineKeyboardMarkup:
    return markup(
        [button("👥 Все пользователи", "pr:audience:all")],
        [button("💎 Только Telegram Premium (+25%)", "pr:audience:premium")],
        [button("Отмена", "menu:promote")],
    )


def promote_confirm() -> InlineKeyboardMarkup:
    return markup(
        [button("💳 Оплатить и запустить", "pr:pay")],
        [button("✖ Отмена", "menu:promote")],
    )


def my_campaigns(items: Sequence[Campaign]) -> InlineKeyboardMarkup:
    rows = [[button(f"#{c.id} {c.title[:26]}", f"pr:view:{c.id}")] for c in items]
    rows.append([button("➕ Новая кампания", "pr:new"), button("🏠 В меню", "menu:home")])
    return markup(*rows)


def campaign_card(campaign: Campaign) -> InlineKeyboardMarkup:
    rows = []
    if campaign.status == CampaignStatus.ACTIVE.value:
        rows.append([button("⏸ Пауза", f"pr:pause:{campaign.id}")])
    elif campaign.status == CampaignStatus.PAUSED.value:
        rows.append([button("▶️ Возобновить", f"pr:resume:{campaign.id}")])
    elif campaign.status == CampaignStatus.AWAITING_PAYMENT.value:
        rows.append([button(f"💳 Оплатить {campaign.xtr_price} XTR", f"pr:payid:{campaign.id}")])
    rows.append([button("‹ Мои кампании", "pr:list"), button("🏠 В меню", "menu:home")])
    return markup(*rows)


def task_card(task: Task, done: bool) -> InlineKeyboardMarkup:
    rows = []
    target = task_target(task)
    if task.kind == TaskKind.SUBSCRIBE.value and target:
        entry = parse_channel_entry(target)
        if entry.has_join_link:
            rows.append([url_button(f"📢 {entry.title}"[:60], entry.url)])
        if not done:
            rows.append([button("🔄 Проверить подписку", f"task:do:{task.id}")])
    elif task.kind == TaskKind.CUSTOM.value and target:
        rows.append([url_button("🔗 Перейти", target)])
        if not done:
            rows.append([button("✅ Получить награду", f"task:do:{task.id}")])
    elif not done:
        label = {
            TaskKind.INVITE.value: "🔄 Проверить рефералов",
            TaskKind.STREAK.value: "🔄 Проверить серию",
        }.get(task.kind, "✅ Выполнить")
        rows.append([button(label, f"task:do:{task.id}")])
    rows.append([button("‹ К заданиям", "menu:tasks"), button("🏠 В меню", "menu:home")])
    return markup(*rows)


def boosts_keyboard(products: Sequence[BoostProduct]) -> InlineKeyboardMarkup:
    rows = [[button(f"{p.title} · {p.xtr_price} XTR", f"boost:view:{p.id}")] for p in products]
    rows.append([button("🏠 В меню", "menu:home")])
    return markup(*rows)


def boost_card(product: BoostProduct) -> InlineKeyboardMarkup:
    return markup(
        [button(f"💳 Купить за {product.xtr_price} XTR", f"boost:buy:{product.id}")],
        [button("‹ К бустам", "menu:boosts"), button("🏠 В меню", "menu:home")],
    )


def top_menu(mode: str) -> InlineKeyboardMarkup:
    refs = "• Рефералы" if mode == "refs" else "Рефералы"
    earn = "• Заработок 7д" if mode == "earn" else "Заработок 7д"
    return markup(
        [button(refs, "menu:top:refs"), button(earn, "menu:top:earn")],
        [button("👥 Моя ссылка", "menu:refs"), button("🏠 В меню", "menu:home")],
    )


def withdraw_keyboard(
    balance: int,
    minimum: int,
    maximum: int,
    *,
    enabled: bool,
    has_open: bool,
    device_url: str | None = None,
) -> InlineKeyboardMarkup:
    rows = []
    if device_url:
        rows.append([device_button(device_url)])
        enabled = False  # verification comes first
    if enabled and not has_open and balance >= minimum:
        cap = min(balance, maximum) if maximum else balance
        presets = [value for value in (minimum, 100, 250, 500) if minimum <= value <= cap]
        chunk = []
        for value in dict.fromkeys(presets):
            chunk.append(button(f"{value} ⭐", f"wd:amt:{value}"))
            if len(chunk) == 3:
                rows.append(chunk)
                chunk = []
        if chunk:
            rows.append(chunk)
        rows.append([button(f"Всё ({cap} ⭐)", f"wd:amt:{cap}"), button("✏️ Своя сумма", "wd:custom")])
    rows.append([button("📄 Мои заявки", "wd:list"), button("🏠 В меню", "menu:home")])
    return markup(*rows)


def withdraw_list_menu(items: Sequence[Withdrawal]) -> InlineKeyboardMarkup:
    rows = []
    for wd in items:
        if wd.status == WithdrawalStatus.PENDING.value:
            rows.append([button(f"✖ Отменить заявку #{wd.id}", f"wd:cancel:{wd.id}")])
    rows.append([button("💸 К выводу", "menu:withdraw"), button("🏠 В меню", "menu:home")])
    return markup(*rows)


def cancel_only(target: str = "menu:home") -> InlineKeyboardMarkup:
    return markup([button("Отмена", target)])


def help_menu(support: str) -> InlineKeyboardMarkup:
    rows = []
    if support:
        handle = support.lstrip("@")
        if handle and " " not in handle:
            rows.append([url_button("📨 Написать в поддержку", f"https://t.me/{handle}")])
    rows.append([button("📄 Условия", "menu:terms"), button("🏠 В меню", "menu:home")])
    return markup(*rows)
