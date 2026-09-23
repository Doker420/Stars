"""User-facing texts (Russian, HTML parse mode)."""

from __future__ import annotations

from collections.abc import Sequence

from app.bot.utils import button, fmt_dt, fmt_duration, fmt_signed, h, markup
from app.config import Settings
from app.db.models import (
    CAMPAIGN_KIND_EMOJI,
    CAMPAIGN_KIND_LABELS,
    CAMPAIGN_STATUS_LABELS,
    LEDGER_KIND_LABELS,
    WITHDRAWAL_STATUS_LABELS,
    Campaign,
    CampaignKind,
    CampaignStatus,
    LedgerEntry,
    PromoCode,
    Task,
    TaskKind,
    User,
    UserBoost,
    Withdrawal,
)
from app.op.manual import parse_channel_entry
from app.services.campaigns import CampaignQuote
from app.services.daily import DailyPreview
from app.services.leaderboard import LeaderRow
from app.services.levels import LevelInfo, format_multiplier, progress_bar
from app.services.partner_tasks import OfferView
from app.services.tasks import task_target

STAR = "⭐"
# Public product name. Kept in one place so rebranding never means grepping the
# codebase again; internal identifiers (package name, log channels, User-Agent)
# intentionally stay "kodostars".
BRAND = "🅢🅣🅐🅡🅑🅞🅞🅢🅣 🅟🅡"


def home(
    user: User,
    balance: int,
    held: int,
    level: LevelInfo,
    boost_bp: int,
    boost_until: str | None,
    link: str,
    device_notice: str = "",
) -> str:
    if level.next_xp is not None:
        level_line = (
            f"🏅 Уровень {level.level} {progress_bar(level.progress(user.xp))} {user.xp}/{level.next_xp} XP"
        )
    else:
        level_line = f"🏅 Уровень {level.level} — максимальный"
    hold = f" · в холде {held} {STAR}" if held else ""
    boost = format_multiplier(boost_bp)
    boost_line = f"✨ Множитель {format_multiplier(level.multiplier_bp)}"
    if boost_bp > 100:
        boost_line += f" · буст {boost}" + (f" до {boost_until}" if boost_until else "")
    notice = f"\n\n{device_notice}" if device_notice else ""
    return (
        f"{STAR} <b>{BRAND}</b>\n\n"
        f"Привет, {h(user.first_name or 'друг')}!\n\n"
        f"💰 Баланс: <b>{balance} {STAR}</b>{hold}\n"
        f"{level_line}\n"
        f"{boost_line}\n"
        f"🔥 Серия ежедневок: {user.streak} дн.\n\n"
        f"🔗 Твоя реферальная ссылка:\n<code>{h(link)}</code>\n\n"
        "Зови друзей, забирай ежедневку, выполняй задания — и выводи Stars."
        f"{notice}"
    )


def device_notice(for_withdraw: bool) -> str:
    tail = " и недоступен вывод" if for_withdraw else ""
    return (
        "🛡 <b>Подтвердите устройство</b> — одна кнопка, две секунды. "
        f"Без этого не засчитываются рефералы{tail}."
    )


def device_twink_notice() -> str:
    return (
        "⚠️ На этом устройстве уже есть другой аккаунт: реферальные бонусы за этот аккаунт "
        "не начисляются. Если это ошибка — напишите в поддержку."
    )


def notify_device_verified(twink: bool, first_time: bool) -> str:
    if twink:
        return (
            "🛡 Устройство проверено.\n\n"
            "⚠️ На нём уже зарегистрирован другой аккаунт, поэтому реферальные бонусы за этот "
            "аккаунт не начисляются. Ежедневка, задания и промокоды работают как обычно. "
            "Если это ошибка — напишите в поддержку."
        )
    if first_time:
        return "🛡 Устройство подтверждено ✅ Рефералы и вывод теперь доступны."
    return "🛡 Устройство подтверждено ✅"


def home_button():
    return markup([button("🏠 В меню", "menu:home")])


def profile(
    user: User,
    balance: int,
    held: int,
    level: LevelInfo,
    refs: dict[int, int],
    ref_earned: int,
    boosts: Sequence[UserBoost],
    boost_titles: dict[int, str],
) -> str:
    status = "🚫 заблокирован" if user.is_banned else "✅ активен"
    if level.next_xp is not None:
        lvl = f"{level.level} ({user.xp}/{level.next_xp} XP)"
    else:
        lvl = f"{level.level} (макс.)"
    lines = [
        "👤 <b>Профиль</b>",
        "",
        f"ID: <code>{user.id}</code>",
        f"Статус: {status}",
        f"💰 Баланс: <b>{balance} {STAR}</b>",
    ]
    if held:
        lines.append(f"⏳ В холде (заявки на вывод): {held} {STAR}")
    lines += [
        f"🏅 Уровень: {lvl} · множитель {format_multiplier(level.multiplier_bp)}",
        f"🔥 Серия: {user.streak} дн.",
        f"⚡ Активность: {user.activity_score}",
        f"👥 Рефералы: L1 — {refs.get(1, 0)}, L2 — {refs.get(2, 0)}",
        f"💎 Заработано с рефералов: {ref_earned} {STAR}",
        f"🎯 Активация рефки: {'да' if user.referral_activated else 'ещё нет'}",
        f"📅 С нами с: {fmt_dt(user.created_at, with_time=False)}",
    ]
    if boosts:
        lines.append("")
        lines.append("🚀 <b>Активные бусты</b>")
        for boost in boosts:
            title = boost_titles.get(boost.product_id, "Буст")
            lines.append(
                f"• {h(title)} {format_multiplier(boost.multiplier_bp)} до {fmt_dt(boost.expires_at)}"
            )
    return "\n".join(lines)


def history(entries: Sequence[LedgerEntry], page: int, total: int, page_size: int) -> str:
    if not entries:
        return "📜 <b>История операций</b>\n\nПока пусто. Заберите ежедневку — это первый плюс!"
    lines = ["📜 <b>История операций</b>", ""]
    for entry in entries:
        label = LEDGER_KIND_LABELS.get(entry.kind, entry.kind)
        lines.append(
            f"{'➕' if entry.amount > 0 else '➖'} <b>{fmt_signed(entry.amount)} {STAR}</b> — "
            f"{h(label)}\n   <i>{fmt_dt(entry.created_at)}</i> · баланс {entry.balance_after}"
        )
    pages = max((total + page_size - 1) // page_size, 1)
    lines.append("")
    lines.append(f"Страница {page + 1} из {pages} · всего операций: {total}")
    return "\n".join(lines)


def referrals(
    user: User,
    link: str,
    stats: dict[int, int],
    activated_l1: int,
    earned: int,
    rank: int | None,
    settings: Settings,
    recent: Sequence[User],
) -> str:
    lines = [
        "👥 <b>Рефералы</b>",
        "",
        f"L1 (твои друзья): <b>{settings.referral_l1_bonus} {STAR}</b> за активацию + "
        f"<b>{settings.referral_l1_percent}%</b> с их заработка.",
    ]
    if settings.referral_levels >= 2:
        lines.append(
            f"L2 (друзья друзей): <b>{settings.referral_l2_bonus} {STAR}</b> + "
            f"<b>{settings.referral_l2_percent}%</b>."
        )
    lines += [
        f"Активация — когда друг набирает {settings.min_referral_activity} очк. активности "
        "(ежедневка, задания, промокод, буст).",
        "",
        f"👤 L1: <b>{stats.get(1, 0)}</b> (активных {activated_l1}) · L2: <b>{stats.get(2, 0)}</b>",
        f"💎 Заработано: <b>{earned} {STAR}</b>",
    ]
    if rank:
        lines.append(f"🏆 Место в топе рефереров: #{rank}")
    lines += ["", "🔗 Ссылка:", f"<code>{h(link)}</code>"]
    if recent:
        lines += ["", "Недавние рефералы:"]
        for ref in recent:
            mark = "✅" if ref.referral_activated else "⏳"
            lines.append(f"{mark} {h(ref.first_name or ref.display_name)}")
    return "\n".join(lines)


def share_text(link: str, signup_bonus: int) -> str:
    bonus = f" Бонус {signup_bonus} ⭐ за старт!" if signup_bonus else ""
    return f"Зарабатывай Telegram Stars за друзей и ежедневки в {BRAND}.{bonus} {link}"


def daily_screen(preview: DailyPreview, settings: Settings) -> str:
    if preview.claimed_today:
        return (
            "🎁 <b>Ежедневная награда</b>\n\n"
            "Сегодня уже забрано ✅\n"
            f"Следующая награда через <b>{fmt_duration(preview.seconds_until_reset)}</b>: "
            f"≈{preview.estimated_reward} {STAR} (серия {preview.streak_if_claimed} дн.).\n\n"
            "Не пропускай день — серия сбросится."
        )
    return (
        "🎁 <b>Ежедневная награда</b>\n\n"
        f"Сегодня: <b>≈{preview.estimated_reward} {STAR}</b> "
        f"(база {preview.base_reward}, серия станет {preview.streak_if_claimed} дн.)\n"
        f"Каждый день серии +{settings.daily_streak_bonus} {STAR}, максимум "
        f"+{settings.daily_streak_cap * settings.daily_streak_bonus} {STAR}.\n\n"
        "Нажми «Забрать»!"
    )


def daily_ok(amount: int, streak: int) -> str:
    return (
        f"🎁 Ежедневная награда: <b>+{amount} {STAR}</b>\n🔥 Серия: {streak} дн. подряд. Возвращайся завтра!"
    )


def daily_wait() -> str:
    return "Сегодня уже забрано. Возвращайтесь завтра — серия вырастет."


def tasks_header(done: int, total: int, partner: int = 0, promo: int = 0) -> str:
    lines = ["📋 <b>Задания</b>", ""]
    # Partner offers lead: they are the best-paying and the monetized ones.
    if partner:
        lines.append(f"🤝 От партнёров: <b>{partner}</b> доступно сейчас — забирайте первыми.")
    if promo:
        lines.append(f"📣 От пользователей: <b>{promo}</b> доступно сейчас.")
    lines.append(f"Внутренние: выполнено {done} из {total}.")
    lines += [
        "",
        "Награда умножается на твой уровень и активный буст. "
        "Часть заданий засчитывается автоматически.",
    ]
    return "\n".join(lines)


# --- partner tasks -------------------------------------------------------------------


def partner_tasks_header(views: Sequence[OfferView]) -> str:
    lines = ["🤝 <b>Задания партнёров</b>", ""]
    if not views:
        lines += [
            "Сейчас для тебя нет доступных заданий от партнёров.",
            "",
            "Они появляются постоянно — загляни позже или выполни внутренние задания.",
        ]
        return "\n".join(lines)
    total = sum(view.reward for view in views)
    lines += [
        f"Доступно заданий: <b>{len(views)}</b> на <b>{total} {STAR}</b>.",
        "",
        "Выбери задание, выполни условие у партнёра и вернись — нажми «Проверить», "
        "награда придёт сразу после подтверждения.",
    ]
    return "\n".join(lines)


def partner_task_card(view: OfferView) -> str:
    offer = view.offer
    lines = [
        f"{view.emoji} <b>{h(offer.title)}</b>",
        "",
        f"Партнёр: {h(view.provider_title)}",
        f"Что нужно: {h(view.action_hint)}",
        f"Награда: <b>+{view.reward} {STAR}</b> (до множителей)",
    ]
    if offer.description:
        lines += ["", h(offer.description)]
    lines += [
        "",
        "1. Нажми кнопку задания и выполни условие.",
        "2. Вернись сюда и нажми «Проверить».",
        "",
        "⚠️ Если отписаться после начисления — награда может быть аннулирована партнёром.",
    ]
    return "\n".join(lines)


def partner_task_done(title: str, amount: int) -> str:
    return (
        f"✅ Задание «{h(title)}» засчитано!\n\n"
        f"Начислено: <b>+{amount} {STAR}</b>\n\n"
        "Забирай следующее — список обновляется постоянно."
    )


def notify_partner_task(title: str, amount: int, provider: str) -> str:
    return f"🤝 Задание партнёра «{h(title)}» засчитано: <b>+{amount} {STAR}</b>"


# --- paid promotion: performer side ----------------------------------------------------


def promo_tasks_header(items: Sequence[Campaign]) -> str:
    lines = ["📣 <b>Задания пользователей</b>", ""]
    if not items:
        lines += [
            "Сейчас активных заказов нет.",
            "",
            "Можешь разместить своё — кнопка «Продвижение» в меню.",
        ]
        return "\n".join(lines)
    lines += [
        f"Доступно: <b>{len(items)}</b>. Их разместили такие же пользователи бота "
        "и оплатили Telegram Stars.",
        "",
        "Выполни условие и получи награду на баланс.",
    ]
    return "\n".join(lines)


def promo_task_card(campaign: Campaign) -> str:
    emoji = CAMPAIGN_KIND_EMOJI.get(campaign.kind, "🔗")
    action = {
        CampaignKind.CHANNEL.value: "Подписаться на канал и не отписываться",
        CampaignKind.BOT.value: "Запустить бота и не блокировать его",
        CampaignKind.POST.value: "Открыть пост и посмотреть его",
    }.get(campaign.kind, "Перейти по ссылке и выполнить условие")
    lines = [
        f"{emoji} <b>{h(campaign.title)}</b>",
        "",
        f"Что нужно: {h(action)}",
        f"Награда: <b>+{campaign.reward} {STAR}</b> (до множителей)",
        f"Осталось мест: <b>{campaign.budget_left}</b> из {campaign.target_count}",
    ]
    if campaign.description:
        lines += ["", h(campaign.description)]
    if campaign.kind in {CampaignKind.CHANNEL.value, CampaignKind.BOT.value} and campaign.check_chat:
        lines += ["", "🔍 Подписка проверяется автоматически."]
    else:
        lines += ["", "🤝 Задание на доверии — не обманывай, иначе бан."]
    return "\n".join(lines)


# --- paid promotion: advertiser side ---------------------------------------------------


def promote_home(active: int, limit: int, enabled: bool) -> str:
    if not enabled:
        return "📣 <b>Продвижение</b>\n\nРаздел временно отключён администратором."
    return (
        "📣 <b>Продвижение за Telegram Stars</b>\n\n"
        "Размести своё задание внутри бота — его увидят живые пользователи, "
        "которые зарабатывают здесь Stars.\n\n"
        "Доступные форматы:\n"
        "📢 Канал — подписка (проверяем через Telegram)\n"
        "🤖 Бот — запуск по твоей ссылке\n"
        "📝 Пост — просмотр публикации\n"
        "👍 Реакция — обычная реакция на пост\n"
        "📊 Опрос — голосование с авто- или ручной проверкой\n"
        "💬 Комментарий — действие с модерацией\n"
        "👁 Story — просмотр истории\n"
        "💎 Premium-реакция — только Telegram Premium\n"
        "🚀 Буст канала — только Telegram Premium\n"
        "🔗 Своё задание — любое действие по ссылке\n\n"
        "Для любого формата доступен Premium-таргетинг (+25% к цене).\n"
        "Платишь только за выполнения: цена = количество × ставка.\n\n"
        f"Твои активные кампании: <b>{active}</b> из {limit}."
    )


def promote_kind_prompt() -> str:
    return "📣 <b>Шаг 1 из 5</b>\n\nЧто продвигаем?"


def promote_title_prompt(kind: str) -> str:
    return (
        f"📣 <b>Шаг 2 из 5</b> · {h(CAMPAIGN_KIND_LABELS.get(kind, kind))}\n\n"
        "Пришли короткое название задания — его увидят исполнители.\n\n"
        "Например: <i>Новости про Telegram Stars</i>"
    )


def promote_target_prompt(kind: str) -> str:
    if kind == CampaignKind.CHANNEL.value:
        return (
            "📣 <b>Шаг 3 из 5</b>\n\n"
            "Пришли канал:\n"
            "• публичный — <code>@myChannel</code>\n"
            "• приватный — <code>-1001234567890|https://t.me/+invite</code>\n\n"
            "⚠️ Добавь этого бота администратором канала, иначе мы не сможем "
            "проверять подписку."
        )
    if kind == CampaignKind.BOT.value:
        return (
            "📣 <b>Шаг 3 из 5</b>\n\n"
            "Пришли ссылку на бота: <code>https://t.me/your_bot</code>\n"
            "Можно с реф-параметром: <code>https://t.me/your_bot?start=kodo</code>"
        )
    if kind == CampaignKind.POST.value:
        return "📣 <b>Шаг 3 из 5</b>\n\nПришли ссылку на пост: <code>https://t.me/channel/123</code>"
    return "📣 <b>Шаг 3 из 5</b>\n\nПришли ссылку, по которой нужно перейти (https://…)."


def promote_description_prompt() -> str:
    return (
        "📣 <b>Шаг 4 из 5</b>\n\n"
        "Добавь описание (до 300 символов) — что увидит исполнитель.\n\n"
        "Или отправь «-», чтобы пропустить."
    )


def promote_count_prompt(kind: str, settings: Settings, *, premium_only: bool = False) -> str:
    reward = settings.campaign_reward(kind)
    low = max(settings.campaign_min_target, 1)
    example = max(low, 100)
    price = settings.campaign_price(kind, example, premium_only=premium_only)
    return (
        "📣 <b>Шаг 5 из 5</b>\n\n"
        f"Сколько выполнений нужно? От {low} до {settings.campaign_max_target}.\n\n"
        f"Исполнитель получает <b>{reward} {STAR}</b> за выполнение.\n"
        f"Пример: {example} выполнений = <b>{price} XTR</b>."
    )


def promote_confirm(
    kind: str,
    title: str,
    url: str,
    description: str,
    quote: CampaignQuote,
    moderation: bool,
) -> str:
    lines = [
        "📣 <b>Проверь заказ</b>",
        "",
        f"Формат: {h(CAMPAIGN_KIND_LABELS.get(kind, kind))}",
        f"Название: {h(title)}",
        f"Ссылка: {h(url)}",
    ]
    if description:
        lines.append(f"Описание: {h(description)}")
    lines += [
        "",
        f"Выполнений: <b>{quote.target_count}</b>",
        f"Награда исполнителю: <b>{quote.reward} {STAR}</b> за штуку",
        f"К оплате: <b>{quote.price_xtr} XTR</b>",
        f"Аудитория: <b>{'только Telegram Premium' if quote.premium_only else 'все пользователи'}</b>",
    ]
    if moderation:
        lines += ["", "После оплаты кампания уйдёт на модерацию (обычно до нескольких часов). "
                  "Если её отклонят — Stars вернём."]
    else:
        lines += ["", "После оплаты кампания запустится сразу."]
    return "\n".join(lines)


def campaign_card(campaign: Campaign, *, owner_view: bool = True) -> str:
    emoji = CAMPAIGN_KIND_EMOJI.get(campaign.kind, "🔗")
    status = CAMPAIGN_STATUS_LABELS.get(campaign.status, campaign.status)
    progress = progress_bar(campaign.done_count / campaign.target_count if campaign.target_count else 0)
    lines = [
        f"{emoji} <b>{h(campaign.title)}</b> · #{campaign.id}",
        "",
        f"Статус: <b>{h(status)}</b>",
        f"Формат: {h(CAMPAIGN_KIND_LABELS.get(campaign.kind, campaign.kind))}",
        f"Прогресс: {progress} {campaign.done_count}/{campaign.target_count}",
        f"Награда: {campaign.reward} {STAR} за выполнение",
    ]
    if owner_view:
        lines.append(f"Оплачено: {campaign.xtr_price} XTR")
    lines.append(f"Ссылка: {h(campaign.url)}")
    if campaign.description:
        lines += ["", h(campaign.description)]
    if campaign.moderation_note:
        lines += ["", f"💬 Комментарий модератора: {h(campaign.moderation_note)}"]
    if campaign.refunded_at:
        lines += ["", "↩️ Оплата возвращена."]
    return "\n".join(lines)


def my_campaigns(items: Sequence[Campaign]) -> str:
    if not items:
        return (
            "📣 <b>Мои кампании</b>\n\n"
            "Пока пусто. Создай первую — нажми «Разместить задание»."
        )
    lines = ["📣 <b>Мои кампании</b>", ""]
    for campaign in items:
        emoji = CAMPAIGN_KIND_EMOJI.get(campaign.kind, "🔗")
        status = CAMPAIGN_STATUS_LABELS.get(campaign.status, campaign.status)
        lines.append(
            f"{emoji} #{campaign.id} {h(campaign.title)} — {h(status)} "
            f"({campaign.done_count}/{campaign.target_count})"
        )
    return "\n".join(lines)


def campaign_payment_prompt(campaign: Campaign) -> str:
    return (
        f"💳 Кампания #{campaign.id} создана.\n\n"
        f"К оплате <b>{campaign.xtr_price} XTR</b>. Нажми кнопку оплаты в сообщении ниже — "
        "счёт выставлен прямо в Telegram."
    )


def notify_campaign_status(campaign: Campaign, status: str, note: str = "") -> str:
    if status == CampaignStatus.ACTIVE.value:
        return (
            f"✅ Кампания #{campaign.id} «{h(campaign.title)}» запущена!\n\n"
            f"Задание уже видят пользователи. Цель: {campaign.target_count} выполнений."
        )
    if status == CampaignStatus.DONE.value:
        return (
            f"🏁 Кампания #{campaign.id} «{h(campaign.title)}» завершена.\n\n"
            f"Выполнений: <b>{campaign.done_count}</b> из {campaign.target_count}. Спасибо!"
        )
    if status == CampaignStatus.REJECTED.value:
        tail = f"\n\nПричина: {h(note)}" if note else ""
        return (
            f"🚫 Кампания #{campaign.id} «{h(campaign.title)}» отклонена модератором.{tail}"
            "\n\nОплаченные Stars вернём — при вопросах пиши в /paysupport."
        )
    return f"📣 Статус кампании #{campaign.id}: {h(CAMPAIGN_STATUS_LABELS.get(status, status))}"



def task_card(task: Task, done: bool) -> str:
    target = task_target(task)
    lines = [f"📌 <b>{h(task.title)}</b>", ""]
    if task.description:
        lines += [h(task.description), ""]
    lines.append(f"Награда: <b>+{task.reward} {STAR}</b> (до множителей)")
    if task.kind == TaskKind.SUBSCRIBE.value and target:
        lines.append(f"Условие: подписка на {h(parse_channel_entry(target).title)}")
    elif task.kind == TaskKind.INVITE.value:
        lines.append(f"Условие: {h(target)} с активацией")
    elif task.kind == TaskKind.STREAK.value:
        lines.append(f"Условие: серия ежедневок {h(target)}")
    elif task.kind == TaskKind.CUSTOM.value and target:
        lines.append("Условие: перейти по ссылке и нажать «Получить»")
    if done:
        lines += ["", "✅ Выполнено"]
    return "\n".join(lines)


def boosts_header(active: Sequence[UserBoost], titles: dict[int, str]) -> str:
    lines = [
        "🚀 <b>Бусты за Telegram Stars</b>",
        "",
        "Оплата в XTR прямо в Telegram. После оплаты пакет или множитель "
        "начисляется мгновенно. Это цифровой товар внутри бота.",
    ]
    if active:
        lines += ["", "Активные:"]
        for boost in active:
            lines.append(
                f"• {h(titles.get(boost.product_id, 'Буст'))} "
                f"{format_multiplier(boost.multiplier_bp)} до {fmt_dt(boost.expires_at)}"
            )
    return "\n".join(lines)


def boost_card(title: str, description: str, price: int, detail: str) -> str:
    return f"🚀 <b>{h(title)}</b>\n\n{h(description)}\n\n{detail}\nЦена: <b>{price} XTR</b>"


def top(
    rows_refs: Sequence[LeaderRow], rows_earn: Sequence[LeaderRow], mode: str, my_rank: int | None
) -> str:
    medals = ["🥇", "🥈", "🥉"]
    if mode == "earn":
        title = "🏆 <b>Топ по заработку за 7 дней</b>"
        rows = rows_earn
        unit = STAR
    else:
        title = "🏆 <b>Топ по рефералам</b>"
        rows = rows_refs
        unit = "реф."
    lines = [title, ""]
    if not rows:
        lines.append("Пока пусто — стань первым!")
    for index, row in enumerate(rows):
        medal = medals[index] if index < 3 else f"{index + 1}."
        lines.append(f"{medal} {h(row.name)} — <b>{row.value}</b> {unit}")
    if mode != "earn" and my_rank:
        lines += ["", f"Твоё место: #{my_rank}"]
    return "\n".join(lines)


def withdraw_home(balance: int, held: int, settings: Settings, open_request: Withdrawal | None) -> str:
    lines = [
        "💸 <b>Вывод Stars</b>",
        "",
        f"Доступно: <b>{balance} {STAR}</b>",
    ]
    if held:
        lines.append(f"В холде: {held} {STAR}")
    limits = f"Минимум: {settings.withdraw_min} {STAR}"
    if settings.withdraw_max:
        limits += f" · максимум: {settings.withdraw_max} {STAR}"
    lines += [limits, f"Кулдаун между заявками: {settings.withdraw_cooldown_hours} ч."]
    if settings.withdraw_min_referrals:
        lines.append(f"Нужно активных рефералов: {settings.withdraw_min_referrals}")
    if not settings.withdraw_enabled:
        lines += ["", "⛔ Вывод временно приостановлен."]
    if open_request is not None:
        lines += [
            "",
            f"Открытая заявка #{open_request.id}: <b>{open_request.amount} {STAR}</b> — "
            f"{WITHDRAWAL_STATUS_LABELS.get(open_request.status, open_request.status)}.",
        ]
    lines += [
        "",
        "Сумма резервируется сразу при создании заявки. Администратор проверяет её и "
        "отправляет Stars вручную (подарком Stars) — обычно в течение суток. "
        "Если заявку отклонят, Stars вернутся на баланс.",
    ]
    return "\n".join(lines)


def withdraw_custom_prompt(balance: int, settings: Settings) -> str:
    maximum = min(balance, settings.withdraw_max) if settings.withdraw_max else balance
    return f"Введите сумму от {settings.withdraw_min} до {maximum} {STAR} числом.\nИли нажмите «Отмена»."


def withdraw_created(wd: Withdrawal) -> str:
    return (
        f"✅ Заявка #{wd.id} на <b>{wd.amount} {STAR}</b> создана.\n"
        "Сумма зарезервирована. Мы уведомим вас, когда администратор её обработает."
    )


def withdraw_list(items: Sequence[Withdrawal]) -> str:
    if not items:
        return "📄 <b>Мои заявки</b>\n\nЗаявок ещё не было."
    lines = ["📄 <b>Мои заявки</b>", ""]
    for wd in items:
        status = WITHDRAWAL_STATUS_LABELS.get(wd.status, wd.status)
        lines.append(f"#{wd.id} · {wd.amount} {STAR} · {status} · {fmt_dt(wd.created_at)}")
        if wd.status == "rejected" and wd.admin_note:
            lines.append(f"   <i>{h(wd.admin_note)}</i>")
    return "\n".join(lines)


def withdraw_status_update(wd: Withdrawal, status: str, note: str) -> str:
    if status == "approved_manual":
        return (
            f"🟢 Заявка #{wd.id} на {wd.amount} {STAR} согласована.\n"
            "Администратор отправит Stars вручную — обычно это подарок Stars в Telegram."
        )
    if status == "sent":
        return f"💸 Заявка #{wd.id}: <b>{wd.amount} {STAR}</b> отправлены. Спасибо, что с нами!"
    if status == "rejected":
        reason = f"\nПричина: {h(note)}" if note else ""
        return f"🔴 Заявка #{wd.id} на {wd.amount} {STAR} отклонена. Stars возвращены на баланс.{reason}"
    return f"Заявка #{wd.id}: статус — {WITHDRAWAL_STATUS_LABELS.get(status, status)}."


def promo_prompt() -> str:
    return "🎟 Введите промокод одним сообщением.\nИли нажмите «Отмена»."


def promo_ok(promo: PromoCode, amount: int) -> str:
    return f"🎟 Промокод <code>{h(promo.code)}</code> активирован: <b>+{amount} {STAR}</b>!"


def op_blocked(provider: str, extra: str = "") -> str:
    tail = f"\n\n{h(extra)}" if extra else ""
    return (
        "🔒 <b>Обязательная подписка</b>\n\n"
        "Чтобы пользоваться ботом, подпишитесь на спонсоров ниже, "
        "затем нажмите «Я подписался»."
        f"{tail}"
    )


def op_intro(provider: str, shown: int) -> str:
    """First-start block: a couple of sponsors, and the bot opens right after."""
    return (
        "👋 <b>Добро пожаловать!</b>\n\n"
        f"Поддержите проект — подпишитесь на {shown} спонсоров ниже. "
        "Это займёт полминуты, а бот откроется сразу: жать «Я подписался» не обязательно.\n\n"
        "🤝 Остальные предложения партнёров ждут вас в разделе «Задания» — "
        "за них уже начисляются звёзды."
    )


def task_digest(tasks: int, campaigns: int, partner: int = 0) -> str:
    """Short periodic nudge listing what is waiting, best-paying first."""
    lines = [f"{STAR} <b>Для вас есть задания</b>\n"]
    if partner:
        lines.append(f"🤝 От партнёров: <b>{partner}</b> — самые выгодные")
    if tasks:
        lines.append(f"📋 Заданий доступно: <b>{tasks}</b>")
    if campaigns:
        lines.append(f"📣 Заданий от пользователей: <b>{campaigns}</b>")
    lines.append("\nЗаходите и забирайте звёзды — это займёт пару минут.")
    return "\n".join(lines)


def digest_toggled(enabled: bool) -> str:
    return (
        "🔔 Напоминания о новых заданиях включены."
        if enabled
        else "🔕 Напоминания отключены. Включить можно в профиле."
    )


def banned(reason: str, support: str = "") -> str:
    contact = f"\nПоддержка: {h(support)}" if support else ""
    return f"🚫 Доступ закрыт.\nПричина: {h(reason)}{contact}"


def banned_short() -> str:
    return "Доступ закрыт"


def help_text(settings: Settings, is_admin: bool) -> str:
    support = f"\n\n📨 Поддержка: {h(settings.support_contact)}" if settings.support_contact else ""
    admin = "\n\n/admin — панель администратора" if is_admin else ""
    return (
        "❓ <b>Как это работает</b>\n\n"
        f"• <b>Ежедневка</b> — каждый день забирай {settings.daily_base_reward}+ {STAR}, "
        "серия увеличивает награду.\n"
        "• <b>Задания</b> — подписки, приглашения, серии. Награда × уровень × буст.\n"
        f"• <b>Рефералы</b> — {settings.referral_l1_bonus} {STAR} за активного друга и "
        f"{settings.referral_l1_percent}% с его заработка (плюс 2-й уровень).\n"
        "• <b>Уровни</b> — XP за любые действия, множитель до ×2.\n"
        "• <b>Бусты</b> — множители и паки за Telegram Stars (XTR).\n"
        f"• <b>Вывод</b> — от {settings.withdraw_min} {STAR}, выплата подарком Stars вручную.\n\n"
        "Команды: /menu — меню, /profile — профиль, /help — эта справка, "
        "/paysupport — вопросы по оплате."
        f"{support}{admin}"
    )


def paysupport(settings: Settings) -> str:
    contact = h(settings.support_contact) if settings.support_contact else "администратору бота"
    return (
        "💳 <b>Поддержка по платежам</b>\n\n"
        "Бусты — цифровые товары, они начисляются сразу после оплаты в Telegram Stars.\n"
        "Если оплата прошла, а буст не появился, или вы хотите вернуть покупку — "
        f"напишите {contact}, укажите ID платежа из чека.\n"
        "Возврат возможен в течение 21 дня, если буст не был использован."
    )


def terms(settings: Settings) -> str:
    contact = f" Контакт: {h(settings.support_contact)}." if settings.support_contact else ""
    return (
        "📄 <b>Условия</b>\n\n"
        "1. Внутренние Stars начисляются за активность и не являются платёжным средством "
        "вне бота.\n"
        "2. Одна учётная запись на человека. Накрутка рефералов ведёт к блокировке и "
        "аннулированию баланса.\n"
        "3. Заявки на вывод проверяются вручную; администратор может запросить "
        "подтверждение активности.\n"
        "4. Покупки бустов — цифровые товары; возврат согласно /paysupport.\n"
        f"5. Администрация вправе менять условия экономики.{contact}"
    )


def payment_done(title: str, charge_id: str) -> str:
    return (
        f"✅ Оплата прошла. <b>{h(title)}</b> активирован.\n"
        f"Чек Telegram: <code>{h(charge_id)}</code>\n"
        "Вопросы по оплате — /paysupport"
    )


def unknown_message() -> str:
    return "Я понимаю только кнопки и команды. Откройте меню: /menu"


def maintenance(settings: Settings) -> str:
    return settings.maintenance_text


def notify_referral_joined(name: str) -> str:
    return f"👥 По твоей ссылке пришёл новый друг: <b>{h(name)}</b>. Бонус будет после активации."


def notify_referral_activated(name: str, level: int, amount: int) -> str:
    who = "твой реферал" if level == 1 else "реферал 2-го уровня"
    return f"💎 {who.capitalize()} <b>{h(name)}</b> активировался: <b>+{amount} {STAR}</b>!"


def notify_task_completed(title: str, amount: int) -> str:
    return f"📋 Задание «{h(title)}» выполнено автоматически: <b>+{amount} {STAR}</b>"


def notify_balance_adjusted(amount: int, reason: str) -> str:
    sign = "начислил" if amount > 0 else "списал"
    tail = f"\nКомментарий: {h(reason)}" if reason else ""
    return f"⚙️ Администратор {sign} <b>{abs(amount)} {STAR}</b>.{tail}"


def notify_refund(xtr: int, revoked: int, title: str) -> str:
    revoked_line = f"\nС баланса списано {revoked} {STAR}." if revoked else ""
    return f"↩️ Покупка «{h(title)}» возвращена: {xtr} XTR вернутся на ваш счёт Telegram.{revoked_line}"
