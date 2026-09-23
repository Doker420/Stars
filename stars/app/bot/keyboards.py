import urllib.parse
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from app.config import config

def get_main_menu_keyboard(
    web_app_url: str, 
    is_admin: bool = False, 
    user_id: int | None = None, 
    username: str | None = None, 
    first_name: str | None = None,
    avatar_url: str | None = None
) -> InlineKeyboardMarkup:
    final_url = web_app_url
    if user_id:
        sep = "&" if "?" in web_app_url else "?"
        params = [f"user_id={user_id}", f"tg_id={user_id}"]
        if username:
            params.append(f"username={urllib.parse.quote(str(username).lstrip('@'))}")
        if first_name:
            params.append(f"first_name={urllib.parse.quote(str(first_name))}")
        if avatar_url:
            params.append(f"avatar_url={urllib.parse.quote(str(avatar_url))}")
        final_url = f"{web_app_url}{sep}{'&'.join(params)}"

    buttons = [
        [
            InlineKeyboardButton(
                text="🌟 Открыть StarVault (Mini App)", 
                web_app=WebAppInfo(url=final_url)
            )
        ],
        [
            InlineKeyboardButton(text="⭐ Купить Звёзды", callback_data="bot_buy_stars"),
            InlineKeyboardButton(text="👑 Telegram Premium", callback_data="bot_buy_premium")
        ],
        [
            InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="menu_deposit"),
            InlineKeyboardButton(text="👤 Мой профиль", callback_data="menu_profile")
        ],
        [
            InlineKeyboardButton(text="🤝 Рефералы (5%)", callback_data="menu_referrals"),
            InlineKeyboardButton(text="🎁 Промокод", callback_data="menu_promo")
        ],
        [
            InlineKeyboardButton(text="📣 Продвигать", callback_data="menu_promote"),
            InlineKeyboardButton(text="📜 Мои заказы", callback_data="menu_orders")
        ],
        [InlineKeyboardButton(text="💬 Поддержка", url="https://t.me/durov")]
    ]
    if is_admin:
        buttons.append([
            InlineKeyboardButton(text="⚡ Админ-панель", callback_data="menu_admin"),
            InlineKeyboardButton(text="🌐 Настройки сайта", callback_data="menu_site")
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_promotion_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Канал", callback_data="promo_format:channel_subscribe"), InlineKeyboardButton(text="🤖 Бот", callback_data="promo_format:bot_start")],
        [InlineKeyboardButton(text="📝 Просмотр поста", callback_data="promo_format:post_view"), InlineKeyboardButton(text="👍 Реакция", callback_data="promo_format:post_reaction")],
        [InlineKeyboardButton(text="📊 Голосование", callback_data="promo_format:poll_vote"), InlineKeyboardButton(text="💬 Комментарий", callback_data="promo_format:post_comment")],
        [InlineKeyboardButton(text="👁 История", callback_data="promo_format:story_view"), InlineKeyboardButton(text="🔗 Своё задание", callback_data="promo_format:custom_link")],
        [InlineKeyboardButton(text="💎 Premium-реакция", callback_data="promo_format:premium_reaction"), InlineKeyboardButton(text="🚀 Буст канала", callback_data="promo_format:channel_boost")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_back")],
    ])


def get_stars_packs_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="50 ⭐ (82.65 ₽)", callback_data="pack_stars:50"),
            InlineKeyboardButton(text="100 ⭐ (165.30 ₽)", callback_data="pack_stars:100")
        ],
        [
            InlineKeyboardButton(text="250 ⭐ (413.25 ₽)", callback_data="pack_stars:250"),
            InlineKeyboardButton(text="500 ⭐ (826.50 ₽)", callback_data="pack_stars:500")
        ],
        [
            InlineKeyboardButton(text="1 000 ⭐ (1 653 ₽)", callback_data="pack_stars:1000"),
            InlineKeyboardButton(text="2 500 ⭐ (4 132 ₽)", callback_data="pack_stars:2500")
        ],
        [
            InlineKeyboardButton(text="5 000 ⭐ (8 265 ₽)", callback_data="pack_stars:5000"),
            InlineKeyboardButton(text="10 000 ⭐ (16 530 ₽)", callback_data="pack_stars:10000")
        ],
        [
            InlineKeyboardButton(text="✏️ Ввести своё количество", callback_data="custom_stars")
        ],
        [
            InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_back")
        ]
    ])

def get_premium_plans_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👑 3 МЕСЯЦА — 1 056.00 ₽ (ПОПУЛЯРНО)", callback_data="pack_prem:3")
        ],
        [
            InlineKeyboardButton(text="👑 6 МЕСЯЦЕВ — 1 410.00 ₽", callback_data="pack_prem:6")
        ],
        [
            InlineKeyboardButton(text="👑 12 МЕСЯЦЕВ — 2 554.00 ₽ (ВЫГОДНЕЕ)", callback_data="pack_prem:12")
        ],
        [
            InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_back")
        ]
    ])

def get_order_payment_keyboard(order_uid: str, cost_rub: float, cost_usdt: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"💰 Оплатить с баланса ({cost_rub:.2f} ₽)", callback_data=f"pay_order:balance:{order_uid}")
        ],
        [
            InlineKeyboardButton(text="🟣 ЮMoney (Карты / СБП)", callback_data=f"pay_order:yoomoney:{order_uid}")
        ],
        [
            InlineKeyboardButton(text=f"🤖 CryptoBot (${cost_usdt:.2f} USDT)", callback_data=f"pay_order:cryptobot:{order_uid}")
        ],
        [
            InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancel_order:{order_uid}")
        ]
    ])

def get_deposit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="100 ₽", callback_data="dep_amt:100"),
            InlineKeyboardButton(text="300 ₽", callback_data="dep_amt:300"),
            InlineKeyboardButton(text="500 ₽", callback_data="dep_amt:500")
        ],
        [
            InlineKeyboardButton(text="1 000 ₽", callback_data="dep_amt:1000"),
            InlineKeyboardButton(text="3 000 ₽", callback_data="dep_amt:3000"),
            InlineKeyboardButton(text="5 000 ₽", callback_data="dep_amt:5000")
        ],
        [
            InlineKeyboardButton(text="✏️ Ввести другую сумму", callback_data="dep_custom")
        ],
        [
            InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_back")
        ]
    ])

def get_payment_method_keyboard(amount: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟣 ЮMoney (Карты/Кошелек)", callback_data=f"pay_meth:yoomoney:{amount}")
        ],
        [
            InlineKeyboardButton(text="🤖 CryptoBot (USDT / TON)", callback_data=f"pay_meth:cryptobot:{amount}")
        ],
        [
            InlineKeyboardButton(text="🔙 Назад к выбору суммы", callback_data="menu_deposit")
        ]
    ])

def get_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Финансовая сводка", callback_data="admin_stats"),
            InlineKeyboardButton(text="📦 Заказы в работе", callback_data="admin_orders")
        ],
        [
            InlineKeyboardButton(text="📢 Задание: Подписка на канал", callback_data="admin_task_channel"),
            InlineKeyboardButton(text="🔄 Сменить провайдера TON/GG", callback_data="admin_toggle_provider")
        ],
        [
            InlineKeyboardButton(text="📈 Наценки Stars & Premium", callback_data="admin_markup"),
            InlineKeyboardButton(text="👥 Поиск юзера / Балансы", callback_data="admin_users")
        ],
        [
            InlineKeyboardButton(text="📢 Массовая рассылка", callback_data="admin_broadcast"),
            InlineKeyboardButton(text="🏷 Промокоды", callback_data="admin_promos")
        ],
        [
            InlineKeyboardButton(text="🛠 Режим техработ", callback_data="admin_maintenance"),
            InlineKeyboardButton(text="🔙 В клиентское меню", callback_data="menu_back")
        ]
    ])

def get_admin_markup_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐ Наценка на Stars", callback_data="set_markup_stars"),
            InlineKeyboardButton(text="👑 Наценка на Premium", callback_data="set_markup_premium")
        ],
        [
            InlineKeyboardButton(text="🔙 В админку", callback_data="menu_admin")
        ]
    ])

def get_admin_task_channel_keyboard(is_enabled: bool = True) -> InlineKeyboardMarkup:
    toggle_text = "🔴 Отключить задание" if is_enabled else "🟢 Включить задание"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔗 Изменить ссылку / username", callback_data="set_task_chan_url")
        ],
        [
            InlineKeyboardButton(text="⭐ Изменить награду (Stars)", callback_data="set_task_chan_reward"),
            InlineKeyboardButton(text=toggle_text, callback_data="toggle_task_chan_status")
        ],
        [
            InlineKeyboardButton(text="🔙 В админку", callback_data="menu_admin")
        ]
    ])

def get_order_action_keyboard(order_uid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Выполнить", callback_data=f"ord_act:complete:{order_uid}"),
            InlineKeyboardButton(text="↩️ Возврат на баланс", callback_data=f"ord_act:refund:{order_uid}")
        ],
        [
            InlineKeyboardButton(text="⚡ Повторить выдачу в TON", callback_data=f"ord_act:retry:{order_uid}"),
            InlineKeyboardButton(text="🔙 Список заказов", callback_data="admin_orders")
        ]
    ])

def get_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
    ])

def get_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_back")]
    ])
