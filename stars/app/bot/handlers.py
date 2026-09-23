import logging
import uuid
import asyncio
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from app.config import config
from app.database import get_db
from app.services.pricing import get_setting, set_setting, calculate_stars_price, calculate_premium_price
from app.services.referrals import PromoService
from app.services.providers import ProviderService
from app.services.payments import PaymentService
from app.services.security import log_audit
from app.bot.keyboards import (
    get_main_menu_keyboard,
    get_promotion_keyboard,
    get_stars_packs_keyboard,
    get_premium_plans_keyboard,
    get_order_payment_keyboard,
    get_deposit_keyboard,
    get_payment_method_keyboard,
    get_admin_keyboard,
    get_admin_markup_keyboard,
    get_admin_task_channel_keyboard,
    get_order_action_keyboard,
    get_cancel_keyboard,
    get_back_keyboard
)
from app.bot.states import (
    BuyStarsStates,
    BuyPremiumStates,
    PromoStates,
    DepositStates,
    AdminBroadcastStates,
    AdminSettingsStates,
    AdminUserStates,
    AdminPromoStates
)

logger = logging.getLogger(__name__)
router = Router()

# ==============================================================
# Helper functions
# ==============================================================
async def get_or_create_user(telegram_id: int, username: str = None, first_name: str = None, referrer_code: str = None, is_premium: bool | None = None) -> dict:
    clean_username = (username or f"user_{telegram_id}").lstrip("@")
    clean_first_name = first_name or clean_username
    ref_code = f"U{uuid.uuid4().hex[:6].upper()}"
    is_admin = 1 if telegram_id in config.ADMIN_TELEGRAM_IDS else 0

    async with get_db() as db:
        referred_by = None
        if referrer_code:
            async with db.execute("SELECT telegram_id FROM users WHERE ref_code = ? OR telegram_id = ?", (referrer_code, referrer_code)) as cur:
                ref_user = await cur.fetchone()
                if ref_user and ref_user["telegram_id"] != telegram_id:
                    referred_by = ref_user["telegram_id"]
                    
        await db.execute("""
        INSERT OR IGNORE INTO users (telegram_id, username, first_name, balance, stars_balance, spins_count, ref_code, referred_by, is_admin)
        VALUES (?, ?, ?, 0.0, 0.0, 1, ?, ?, ?)
        """, (telegram_id, clean_username, clean_first_name, ref_code, referred_by, is_admin))
        
        await db.execute("""
        UPDATE users SET username = ?, first_name = ?, telegram_is_premium = COALESCE(?, telegram_is_premium) WHERE telegram_id = ?
        """, (clean_username, clean_first_name, int(is_premium) if is_premium is not None else None, telegram_id))
        await db.commit()
        
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)) as cur:
            return dict(await cur.fetchone())

# ==============================================================
# 1. COMMANDS: /start, /buy, /stars, /premium, /profile, /deposit, /ref, /promo, /help
# ==============================================================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    try:
        from aiogram.types import MenuButtonWebApp, WebAppInfo
        import urllib.parse
        clean_u = urllib.parse.quote(str(message.from_user.username or '').lstrip('@'))
        clean_n = urllib.parse.quote(str(message.from_user.first_name or ''))
        sep = '&' if '?' in config.WEB_APP_URL else '?'
        m_url = f"{config.WEB_APP_URL}{sep}user_id={message.from_user.id}&tg_id={message.from_user.id}&username={clean_u}&first_name={clean_n}"
        await message.bot.set_chat_menu_button(
            chat_id=message.chat.id,
            menu_button=MenuButtonWebApp(text="🌟 Mini App", web_app=WebAppInfo(url=m_url))
        )
    except Exception:
        pass
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []
    ref_code = None
    if args and args[0].startswith("ref_"):
        ref_code = args[0].replace("ref_", "").strip()
        
    user = await get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        referrer_code=ref_code,
        is_premium=bool(getattr(message.from_user, "is_premium", False))
    )
    
    provider = await get_setting("active_provider", "mystars")
    p_name = "⚡️ Fragment" if provider == "mystars" else "⚡️ Fragment"
    
    text = (
        f"💫 <b>Добро пожаловать в StarVault!</b>\n\n"
        f"⚡ <i>Моментальная доставка Telegram Stars и Premium на любой @username без передачи паролей.</i>\n\n"
        f"⭐ <b>Звёзды:</b> от 50 до 1 000 000 ⭐\n"
        f"👑 <b>Telegram Premium:</b> 3 / 6 / 12 месяцев\n"
        f"💳 <b>Оплата:</b> Баланс, СБП / Карты, ЮMoney, CryptoBot\n"
        f"📡 <b>Шлюз выдачи:</b> {p_name}\n\n"
        f"👤 Ваш баланс: <b>{user['balance']:.2f} ₽</b> (⭐ {user.get('stars_balance', 0):.0f} Stars)\n"
        f"🤝 Партнёрский кэшбэк: <b>{user.get('custom_ref_percent') or 5}%</b>"
    )
    
    is_admin = bool(user["is_admin"]) or message.from_user.id in config.ADMIN_TELEGRAM_IDS
    await message.answer(
        text, 
        reply_markup=get_main_menu_keyboard(
            config.WEB_APP_URL, 
            is_admin=is_admin,
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name
        ), 
        parse_mode="HTML"
    )

@router.message(Command("buy", "shop", "app"))
async def cmd_buy(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    is_admin = bool(user["is_admin"]) or message.from_user.id in config.ADMIN_TELEGRAM_IDS
    await message.answer(
        "🌟 Нажмите кнопку ниже, чтобы открыть официальный Mini App StarVault:",
        reply_markup=get_main_menu_keyboard(
            config.WEB_APP_URL, 
            is_admin=is_admin,
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name
        )
    )

@router.message(Command("stars"))
async def cmd_stars(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "⭐ <b>Покупка Telegram Stars</b>\n\n"
        "Выберите готовый пакет или введите произвольное количество:",
        reply_markup=get_stars_packs_keyboard(),
        parse_mode="HTML"
    )

@router.message(Command("premium"))
async def cmd_premium(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👑 <b>Оформление Telegram Premium</b>\n\n"
        "Выберите срок подписки на официальный подарок (Gift):",
        reply_markup=get_premium_plans_keyboard(),
        parse_mode="HTML"
    )

@router.message(Command("profile"))
async def cmd_profile(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) as cnt FROM orders WHERE user_id = ?", (user["id"],)) as cur:
            orders_cnt = (await cur.fetchone())["cnt"]
            
    text = (
        f"👤 <b>Личный кабинет StarVault</b>\n\n"
        f"🆔 Telegram ID: <code>{user['telegram_id']}</code>\n"
        f"👤 Username: <b>@{user['username']}</b>\n"
        f"💰 Баланс: <b>{user['balance']:.2f} ₽</b>\n"
        f"⭐ Звёзды на балансе: <b>{user.get('stars_balance', 0):.0f} Stars</b>\n"
        f"🌀 Спинов в Колесе: <b>{user.get('spins_count', 1)}</b>\n"
        f"📦 Оформлено заказов: <b>{orders_cnt}</b>\n"
        f"🔑 Реферальный код: <code>{user['ref_code']}</code>"
    )
    await message.answer(text, reply_markup=get_deposit_keyboard(), parse_mode="HTML")

@router.message(Command("deposit", "topup"))
async def cmd_deposit(message: Message):
    await message.answer("💳 Выберите сумму для пополнения баланса:", reply_markup=get_deposit_keyboard())

@router.message(Command("ref", "referrals"))
async def cmd_referrals(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) as cnt FROM users WHERE referred_by = ?", (user["telegram_id"],)) as cur:
            ref_count = (await cur.fetchone())["cnt"]
        async with db.execute("SELECT COALESCE(SUM(amount_rub), 0.0) as earned FROM transactions WHERE user_id = ? AND type = 'ref_reward'", (user["id"],)) as cur:
            ref_earned = (await cur.fetchone())["earned"]
            
    ref_link = f"https://t.me/StarVaultRoBot?start=ref_{user['telegram_id']}"
    ref_percent = user.get("custom_ref_percent") or 5.0
    
    text = (
        f"🤝 <b>Партнёрская программа StarVault</b>\n\n"
        f"Приглашайте друзей и получайте <b>{ref_percent}%</b> от каждого их пополнения баланса пожизненно!\n\n"
        f"👥 Приглашено друзей: <b>{ref_count}</b>\n"
        f"💰 Заработано с рефералов: <b>{ref_earned:.2f} ₽</b>\n\n"
        f"🔗 Ваша реферальная ссылка:\n<code>{ref_link}</code>"
    )
    await message.answer(text, reply_markup=get_back_keyboard(), parse_mode="HTML")

@router.message(Command("promo"))
async def cmd_promo(message: Message, state: FSMContext):
    await state.set_state(PromoStates.waiting_for_code)
    await message.answer("🎁 Введите промокод для активации:", reply_markup=get_cancel_keyboard())

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        await message.answer("❌ Доступ запрещён.")
        return
        
    provider = await get_setting("active_provider", "mystars")
    markup_stars = await get_setting("stars_markup_percent", "14.0")
    markup_prem = await get_setting("premium_markup_percent", "12.0")
    chan_url = await get_setting("task_channel_url", "https://t.me/starvault_news")
    
    text = (
        f"⚡ <b>Админ-панель управления StarVault</b>\n\n"
        f"📡 <b>Активный шлюз:</b> {provider.upper()} (TON FaaS)\n"
        f"⭐ <b>Наценка на Stars:</b> +{markup_stars}%\n"
        f"👑 <b>Наценка на Premium:</b> +{markup_prem}%\n"
        f"📢 <b>Канал для задания:</b> {chan_url}\n"
        f"🛡 <b>База:</b> SQLite WAL-mode (ACID)"
    )
    await message.answer(text, reply_markup=get_admin_keyboard(), parse_mode="HTML")

# ==============================================================
# 2. STARS PURCHASE FLOW (IN BOT)
# ==============================================================
@router.callback_query(F.data == "bot_buy_stars")
async def cb_buy_stars_menu(query: CallbackQuery, state: FSMContext):
    await state.clear()
    await query.message.edit_text(
        "⭐ <b>Покупка Telegram Stars</b>\n\nВыберите пакет Звёзд или укажите своё количество:",
        reply_markup=get_stars_packs_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("pack_stars:"))
async def cb_pack_stars(query: CallbackQuery, state: FSMContext):
    amount = int(query.data.split(":")[1])
    await state.update_data(stars_amount=amount)
    await state.set_state(BuyStarsStates.waiting_for_recipient)
    await query.message.edit_text(
        f"⭐ Вы выбрали <b>{amount:,} Stars</b>.\n\n"
        f"👤 Введите <b>Telegram @username</b> получателя (например: <code>@durov</code>):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "custom_stars")
async def cb_custom_stars(query: CallbackQuery, state: FSMContext):
    await state.set_state(BuyStarsStates.waiting_for_amount)
    await query.message.edit_text(
        "✏️ Введите количество Stars (от <b>50</b> до <b>1 000 000</b>):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )

@router.message(StateFilter(BuyStarsStates.waiting_for_amount))
async def process_custom_stars_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text.strip().replace(" ", "").replace("⭐", ""))
        if amount < 50 or amount > 1000000:
            await message.answer("❌ Количество Stars должно быть в диапазоне от 50 до 1 000 000. Попробуйте снова:")
            return
    except ValueError:
        await message.answer("❌ Введите целое число (например: 250):")
        return
        
    await state.update_data(stars_amount=amount)
    await state.set_state(BuyStarsStates.waiting_for_recipient)
    await message.answer(
        f"⭐ Вы выбрали <b>{amount:,} Stars</b>.\n\n"
        f"👤 Введите <b>Telegram @username</b> получателя (например: <code>@durov</code>):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )

@router.message(StateFilter(BuyStarsStates.waiting_for_recipient))
async def process_stars_recipient(message: Message, state: FSMContext):
    username = message.text.strip().lstrip("@").strip()
    if not username or len(username) < 3:
        await message.answer("❌ Укажите корректный Telegram @username (например: <code>@durov</code>):")
        return
        
    data = await state.get_data()
    amount = data["stars_amount"]
    pricing = await calculate_stars_price(amount)
    
    order_uid = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    active_provider = await get_setting("active_provider", "mystars")
    
    user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    
    async with get_db() as db:
        await db.execute("""
        INSERT INTO orders (
            order_uid, user_id, type, item_amount, recipient_username,
            cost_rub, cost_usdt, cost_ton, base_cost_rub, margin_rub,
            provider, status, payment_method
        ) VALUES (?, ?, 'stars', ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'pending')
        """, (
            order_uid, user["id"], amount, username,
            pricing["total_rub"], pricing["total_usdt"], pricing["total_ton"],
            pricing["base_cost_rub"], pricing["margin_rub"], active_provider
        ))
        await db.commit()
        
    await state.clear()
    
    text = (
        f"📦 <b>Заказ {order_uid} сформирован!</b>\n\n"
        f"🎁 Товар: <b>{amount:,} Telegram Stars ⭐</b>\n"
        f"👤 Получатель: <b>@{username}</b>\n"
        f"💵 К оплате: <b>{pricing['total_rub']:.2f} ₽</b> (${pricing['total_usdt']:.2f} USDT)\n"
        f"📡 Автодоставка: <b>{active_provider.upper()} TON FaaS</b>\n\n"
        f"Выберите удобный способ оплаты:"
    )
    await message.answer(text, reply_markup=get_order_payment_keyboard(order_uid, pricing["total_rub"], pricing["total_usdt"]), parse_mode="HTML")

# ==============================================================
# 3. PREMIUM PURCHASE FLOW (IN BOT)
# ==============================================================
@router.callback_query(F.data == "bot_buy_premium")
async def cb_buy_premium_menu(query: CallbackQuery, state: FSMContext):
    await state.clear()
    await query.message.edit_text(
        "👑 <b>Telegram Premium подписка</b>\n\nВыберите срок подарочной подписки:",
        reply_markup=get_premium_plans_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("pack_prem:"))
async def cb_pack_premium(query: CallbackQuery, state: FSMContext):
    months = int(query.data.split(":")[1])
    await state.update_data(premium_months=months)
    await state.set_state(BuyPremiumStates.waiting_for_recipient)
    await query.message.edit_text(
        f"👑 Вы выбрали <b>Telegram Premium на {months} мес.</b>\n\n"
        f"👤 Введите <b>Telegram @username</b> получателя (например: <code>@durov</code>):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )

@router.message(StateFilter(BuyPremiumStates.waiting_for_recipient))
async def process_premium_recipient(message: Message, state: FSMContext):
    username = message.text.strip().lstrip("@").strip()
    if not username or len(username) < 3:
        await message.answer("❌ Укажите корректный Telegram @username (например: <code>@durov</code>):")
        return
        
    data = await state.get_data()
    months = data["premium_months"]
    pricing = await calculate_premium_price(months)
    
    order_uid = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    active_provider = await get_setting("active_provider", "mystars")
    
    user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    
    async with get_db() as db:
        await db.execute("""
        INSERT INTO orders (
            order_uid, user_id, type, item_amount, recipient_username,
            cost_rub, cost_usdt, cost_ton, base_cost_rub, margin_rub,
            provider, status, payment_method
        ) VALUES (?, ?, 'premium', ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'pending')
        """, (
            order_uid, user["id"], months, username,
            pricing["total_rub"], pricing["total_usdt"], pricing["total_ton"],
            pricing["base_cost_rub"], pricing["margin_rub"], active_provider
        ))
        await db.commit()
        
    await state.clear()
    
    text = (
        f"📦 <b>Заказ {order_uid} сформирован!</b>\n\n"
        f"🎁 Товар: <b>Telegram Premium ({months} мес.) 👑</b>\n"
        f"👤 Получатель: <b>@{username}</b>\n"
        f"💵 К оплате: <b>{pricing['total_rub']:.2f} ₽</b> (${pricing['total_usdt']:.2f} USDT)\n"
        f"📡 Автодоставка: <b>{active_provider.upper()} TON FaaS</b>\n\n"
        f"Выберите удобный способ оплаты:"
    )
    await message.answer(text, reply_markup=get_order_payment_keyboard(order_uid, pricing["total_rub"], pricing["total_usdt"]), parse_mode="HTML")

# ==============================================================
# 4. ORDER PAYMENT HANDLERS
# ==============================================================
@router.callback_query(F.data.startswith("pay_order:"))
async def cb_pay_order(query: CallbackQuery):
    parts = query.data.split(":")
    method = parts[1]
    order_uid = parts[2]
    
    async with get_db() as db:
        async with db.execute("SELECT * FROM orders WHERE order_uid = ?", (order_uid,)) as cur:
            order = await cur.fetchone()
            if not order:
                await query.answer("❌ Заказ не найден", show_alert=True)
                return
                
        async with db.execute("SELECT * FROM users WHERE id = ?", (order["user_id"],)) as cur:
            user = await cur.fetchone()
            
    if order["status"] == "completed":
        await query.answer("✅ Этот заказ уже успешно оплачен и выполнен!", show_alert=True)
        return
        
    if method == "balance":
        if user["balance"] < order["cost_rub"]:
            need = order["cost_rub"] - user["balance"]
            await query.answer(f"❌ Недостаточно средств на балансе. Не хватает {need:.2f} ₽.", show_alert=True)
            return
            
        async with get_db() as db:
            await db.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (order["cost_rub"], user["id"]))
            await db.execute("""
            INSERT INTO transactions (user_id, type, amount_rub, description, order_id)
            VALUES (?, 'purchase', ?, ?, ?)
            """, (user["id"], -order["cost_rub"], f"Оплата заказа {order_uid}", order["id"]))
            await db.execute("UPDATE orders SET status = 'processing', payment_method = 'balance' WHERE order_uid = ?", (order_uid,))
            await db.commit()
            
        await query.message.edit_text("⏳ <b>Оплата с баланса принята!</b>\n\nИдёт автоматическая отправка через TON Smart-contract...", parse_mode="HTML")
        
        # Deliver instantly
        res = await ProviderService.deliver_order(
            order_id=order["order_uid"],
            order_type=order["type"],
            amount=order["item_amount"],
            recipient_username=order["recipient_username"]
        )
        
        async with get_db() as db:
            if res.get("success"):
                await db.execute("UPDATE orders SET status = 'completed', provider = ?, provider_order_id = ?, updated_at = CURRENT_TIMESTAMP WHERE order_uid = ?", (res.get("provider"), res.get("provider_order_id"), order_uid))
                await db.commit()
                text = (
                    f"🎉 <b>ЗАКАЗ {order_uid} УСПЕШНО ДОСТАВЛЕН!</b>\n\n"
                    f"🎁 Товар: <b>{order['item_amount']} {order['type']}</b>\n"
                    f"👤 Получатель: <b>@{order['recipient_username']}</b>\n"
                    f"⚡ Хеш транзакции TON: <code>{res.get('provider_order_id')}</code>\n\n"
                    f"<i>Спасибо за покупку в StarVault!</i>"
                )
                await query.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="HTML")
            else:
                await db.execute("UPDATE orders SET status = 'failed', error_message = ? WHERE order_uid = ?", (res.get("message"), order_uid))
                await db.commit()
                await query.message.edit_text(f"⚠️ Ошибка доставки: {res.get('message')}. Средства остаются на проверке поддержки.", reply_markup=get_back_keyboard())
                
    elif method == "yoomoney":
        pay_info = await PaymentService.create_payment(
            order_id=order_uid,
            amount_rub=order["cost_rub"],
            amount_usdt=order["cost_usdt"],
            method="yoomoney",
            user_id=user["id"],
            description=f"Оплата заказа {order_uid}"
        )
        url = pay_info.get("payment_url")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟣 Перейти к оплате ЮMoney / Картой", url=url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_pay:yoomoney:{order_uid}")],
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_back")]
        ])
        await query.message.edit_text(f"💳 <b>Оплата через ЮMoney (Карты / СБП)</b>\n\nСумма: <b>{order['cost_rub']:.2f} ₽</b>\n\nНажмите кнопку ниже для перехода к оплате:", reply_markup=kb, parse_mode="HTML")
        
    elif method == "cryptobot":
        pay_info = await PaymentService.create_payment(
            order_id=order_uid,
            amount_rub=order["cost_rub"],
            amount_usdt=order["cost_usdt"],
            method="cryptobot",
            user_id=user["id"],
            description=f"Оплата заказа {order_uid}"
        )
        url = pay_info.get("payment_url")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🤖 Оплатить в CryptoBot", url=url)],
            [InlineKeyboardButton(text="🔄 Проверить статус", callback_data=f"check_pay:cryptobot:{order_uid}")],
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_back")]
        ])
        await query.message.edit_text(f"🤖 <b>Оплата через CryptoBot</b>\n\nСумма: <b>${order['cost_usdt']:.2f} USDT</b> ({order['cost_rub']:.2f} ₽)\n\nНажмите кнопку ниже для быстрой оплаты в Telegram:", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("cancel_order:"))
async def cb_cancel_order(query: CallbackQuery):
    order_uid = query.data.split(":")[1]
    async with get_db() as db:
        await db.execute("UPDATE orders SET status = 'cancelled' WHERE order_uid = ? AND status = 'pending'", (order_uid,))
        await db.commit()
    await query.message.edit_text(f"❌ Заказ {order_uid} отменён.", reply_markup=get_back_keyboard())

# ==============================================================
# 5. BALANCE DEPOSIT & PROMO & PROFILE
# ==============================================================
@router.callback_query(F.data == "cancel_action")
async def cb_cancel_action(query: CallbackQuery, state: FSMContext):
    await state.clear()
    await query.message.edit_text("❌ Действие отменено.", reply_markup=get_back_keyboard())

@router.callback_query(F.data == "menu_back")
async def cb_menu_back(query: CallbackQuery, state: FSMContext):
    await state.clear()
    user = await get_or_create_user(query.from_user.id, query.from_user.username, query.from_user.first_name)
    is_admin = bool(user["is_admin"]) or query.from_user.id in config.ADMIN_TELEGRAM_IDS
    await query.message.edit_text(
        "💫 <b>Главное меню StarVault</b>\n\nВыберите нужный раздел:",
        reply_markup=get_main_menu_keyboard(
            config.WEB_APP_URL, 
            is_admin=is_admin,
            user_id=query.from_user.id,
            username=query.from_user.username,
            first_name=query.from_user.first_name
        ),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "menu_promote")
async def cb_menu_promote(query: CallbackQuery):
    user = await get_or_create_user(query.from_user.id, query.from_user.username, query.from_user.first_name)
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) count FROM campaigns WHERE owner_user_id=? AND status='active'", (user["id"],)) as cur:
            active = (await cur.fetchone())["count"]
    text = (
        "📣 <b>Продвижение за Telegram Stars</b>\n\n"
        "Размести своё задание внутри бота — его увидят живые пользователи, которые зарабатывают здесь Stars.\n\n"
        "<b>Доступные форматы:</b>\n"
        "📢 Канал — подписка с проверкой через Telegram\n"
        "🤖 Бот — запуск по deep-link\n"
        "📝 Пост — просмотр публикации\n"
        "👍 Реакция — обычная реакция на пост\n"
        "📊 Опрос — голосование с авто- или ручной проверкой\n"
        "💬 Комментарий — действие с модерацией\n"
        "👁 История — просмотр Telegram Story\n"
        "💎 Premium-реакция — только пользователи Telegram Premium\n"
        "🚀 Буст канала — только пользователи Telegram Premium\n"
        "🔗 Своё задание — любое действие по безопасной ссылке\n\n"
        "Можно включить <b>таргет только на Premium-аудиторию</b> для любого формата.\n"
        "Платишь только за выполнения: <b>цена = количество × ставка</b>.\n\n"
        f"Твои активные кампании: <b>{active}</b> из 3."
    )
    await query.message.edit_text(text, reply_markup=get_promotion_keyboard(), parse_mode="HTML")
    await query.answer()


@router.callback_query(F.data.startswith("promo_format:"))
async def cb_promotion_format(query: CallbackQuery):
    format_id = query.data.split(":", 1)[1]
    from app.web.routes_campaigns import FORMATS
    item = FORMATS.get(format_id)
    if not item:
        await query.answer("Формат не найден", show_alert=True)
        return
    premium = "\n💎 Только Telegram Premium" if item.get("premium_required") else "\n🎯 Можно включить Premium-таргетинг (+25%)"
    await query.answer(f"{item['title']}\nСтавка: {item['rate']:g} ⭐ за выполнение{premium}", show_alert=True)


@router.callback_query(F.data == "menu_profile")
async def cb_profile(query: CallbackQuery):
    user = await get_or_create_user(query.from_user.id, query.from_user.username, query.from_user.first_name)
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) as cnt FROM orders WHERE user_id = ?", (user["id"],)) as cur:
            orders_cnt = (await cur.fetchone())["cnt"]
            
    text = (
        f"👤 <b>Личный кабинет StarVault</b>\n\n"
        f"🆔 Telegram ID: <code>{user['telegram_id']}</code>\n"
        f"👤 Username: <b>@{user['username']}</b>\n"
        f"💰 Баланс: <b>{user['balance']:.2f} ₽</b>\n"
        f"⭐ Звёзды: <b>{user.get('stars_balance', 0):.0f} Stars</b>\n"
        f"🌀 Спинов в Колесе: <b>{user.get('spins_count', 1)}</b>\n"
        f"📦 Оформлено заказов: <b>{orders_cnt}</b>\n"
        f"🔑 Реферальный код: <code>{user['ref_code']}</code>"
    )
    await query.message.edit_text(text, reply_markup=get_deposit_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "menu_deposit")
async def cb_deposit(query: CallbackQuery):
    await query.message.edit_text("💳 Выберите сумму для пополнения баланса:", reply_markup=get_deposit_keyboard())

@router.callback_query(F.data.startswith("dep_amt:"))
async def cb_deposit_amount(query: CallbackQuery):
    amt = float(query.data.split(":")[1])
    await query.message.edit_text(
        f"💳 Пополнение баланса на сумму <b>{amt:.2f} ₽</b>\n\nВыберите способ оплаты:",
        reply_markup=get_payment_method_keyboard(amt),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "dep_custom")
async def cb_deposit_custom(query: CallbackQuery, state: FSMContext):
    await state.set_state(DepositStates.waiting_for_amount)
    await query.message.edit_text(
        "✏️ Введите желаемую сумму пополнения в рублях (от 50 до 100 000 ₽):",
        reply_markup=get_cancel_keyboard()
    )

@router.message(StateFilter(DepositStates.waiting_for_amount))
async def process_custom_deposit_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount < 50 or amount > 100000:
            await message.answer("❌ Сумма пополнения должна быть от 50 до 100 000 ₽. Попробуйте снова:")
            return
    except ValueError:
        await message.answer("❌ Введите корректное число (например: 500):")
        return
        
    await state.clear()
    await message.answer(
        f"💳 Пополнение баланса на сумму <b>{amount:.2f} ₽</b>\n\nВыберите способ оплаты:",
        reply_markup=get_payment_method_keyboard(amount),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("pay_meth:"))
async def cb_pay_method_chosen(query: CallbackQuery):
    parts = query.data.split(":")
    method = parts[1]
    amount = float(parts[2])
    
    user = await get_or_create_user(query.from_user.id, query.from_user.username, query.from_user.first_name)
    order_id = f"DEP-{uuid.uuid4().hex[:8].upper()}"
    usdt_rate = float(await get_setting("usdt_rub_rate", "92.5"))
    amount_usdt = round(amount / usdt_rate, 2)
    
    pay_info = await PaymentService.create_payment(
        order_id=order_id,
        amount_rub=amount,
        amount_usdt=amount_usdt,
        method=method,
        user_id=user["id"],
        description=f"Пополнение баланса #{user['id']} на {amount:.2f} ₽"
    )
    
    url = pay_info.get("payment_url")
    btn_text = "🟣 Перейти к оплате (ЮMoney)" if method == "yoomoney" else "🤖 Оплатить в CryptoBot"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn_text, url=url)],
        [InlineKeyboardButton(text="🔄 Проверить зачисление", callback_data=f"check_dep:{method}:{order_id}:{amount}")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_back")]
    ])
    
    await query.message.edit_text(
        f"💳 <b>Счёт на пополнение {order_id}</b>\n\n"
        f"Сумма: <b>{amount:.2f} ₽</b>\n"
        f"Платёжная система: <b>{method.upper()}</b>\n\n"
        f"Нажмите кнопку ниже для завершения оплаты:",
        reply_markup=kb,
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("check_dep:"))
async def cb_check_deposit(query: CallbackQuery):
    parts = query.data.split(":")
    method = parts[1]
    order_id = parts[2]
    amount = float(parts[3])
    
    user = await get_or_create_user(query.from_user.id, query.from_user.username, query.from_user.first_name)
    
    # Check status
    st = await PaymentService.check_payment_status(method, order_id)
    if st.get("status") == "paid":
        async with get_db() as db:
            await db.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, user["id"]))
            await db.execute("""
            INSERT INTO transactions (user_id, type, amount_rub, description)
            VALUES (?, 'deposit', ?, ?)
            """, (user["id"], amount, f"Пополнение через {method.upper()} ({order_id})"))
            await db.commit()
            
        await query.message.edit_text(f"🎉 <b>Баланс успешно пополнен на {amount:.2f} ₽!</b>", reply_markup=get_back_keyboard(), parse_mode="HTML")
    else:
        await query.answer("⏳ Оплата ещё не поступила. Попробуйте через 10-20 секунд.", show_alert=True)

@router.callback_query(F.data == "menu_referrals")
async def cb_referrals(query: CallbackQuery):
    user = await get_or_create_user(query.from_user.id, query.from_user.username, query.from_user.first_name)
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) as cnt FROM users WHERE referred_by = ?", (user["telegram_id"],)) as cur:
            ref_count = (await cur.fetchone())["cnt"]
        async with db.execute("SELECT COALESCE(SUM(amount_rub), 0.0) as earned FROM transactions WHERE user_id = ? AND type = 'ref_reward'", (user["id"],)) as cur:
            ref_earned = (await cur.fetchone())["earned"]
            
    ref_link = f"https://t.me/StarVaultRoBot?start=ref_{user['telegram_id']}"
    ref_percent = user.get("custom_ref_percent") or 5.0
    
    text = (
        f"🤝 <b>Партнёрская программа StarVault</b>\n\n"
        f"Приглашайте друзей и получайте <b>{ref_percent}%</b> от каждого их пополнения баланса пожизненно!\n\n"
        f"👥 Приглашено друзей: <b>{ref_count}</b>\n"
        f"💰 Заработано с рефералов: <b>{ref_earned:.2f} ₽</b>\n\n"
        f"🔗 Ваша реферальная ссылка:\n<code>{ref_link}</code>"
    )
    await query.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "menu_promo")
async def cb_promo(query: CallbackQuery, state: FSMContext):
    await state.set_state(PromoStates.waiting_for_code)
    await query.message.edit_text("🎁 <b>Введите промокод</b> для активации баланса или скидки:", reply_markup=get_cancel_keyboard(), parse_mode="HTML")

@router.message(StateFilter(PromoStates.waiting_for_code))
async def process_promo_code(message: Message, state: FSMContext):
    code = message.text.strip().upper()
    user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    
    res = await PromoService.apply_promo(code, user["id"])
    await state.clear()
    
    if res.get("success"):
        await message.answer(f"🎉 {res.get('message')}", reply_markup=get_back_keyboard())
    else:
        await message.answer(f"❌ {res.get('message')}", reply_markup=get_back_keyboard())

@router.callback_query(F.data == "menu_orders")
async def cb_orders_history(query: CallbackQuery):
    user = await get_or_create_user(query.from_user.id, query.from_user.username, query.from_user.first_name)
    async with get_db() as db:
        async with db.execute("SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user["id"],)) as cur:
            orders = [dict(r) for r in await cur.fetchall()]
            
    if not orders:
        await query.message.edit_text("📦 У вас пока нет оформленных заказов.", reply_markup=get_back_keyboard())
        return
        
    lines = ["📜 <b>Ваши последние заказы:</b>\n"]
    for o in orders:
        st_icon = "✅" if o["status"] == "completed" else ("⏳" if o["status"] == "pending" else "⚠️")
        lines.append(f"{st_icon} <b>{o['order_uid']}</b> — {o['item_amount']} {o['type']} для @{o['recipient_username']} ({o['cost_rub']:.2f} ₽)")
        
    await query.message.edit_text("\n".join(lines), reply_markup=get_back_keyboard(), parse_mode="HTML")

# ==============================================================
# 6. ADMIN PANEL HANDLERS
# ==============================================================
@router.callback_query(F.data == "menu_admin")
async def cb_admin_menu(query: CallbackQuery, state: FSMContext):
    await state.clear()
    if query.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        await query.answer("❌ Доступ запрещён", show_alert=True)
        return
        
    provider = await get_setting("active_provider", "mystars")
    markup_stars = await get_setting("stars_markup_percent", "14.0")
    markup_prem = await get_setting("premium_markup_percent", "12.0")
    chan_url = await get_setting("task_channel_url", "https://t.me/starvault_news")
    
    text = (
        f"⚡ <b>Админ-панель управления StarVault</b>\n\n"
        f"📡 <b>Активный шлюз:</b> {provider.upper()} (TON FaaS)\n"
        f"⭐ <b>Наценка на Stars:</b> +{markup_stars}%\n"
        f"👑 <b>Наценка на Premium:</b> +{markup_prem}%\n"
        f"📢 <b>Канал заданий:</b> {chan_url}\n"
        f"🛡 <b>База:</b> SQLite WAL-mode (ACID)"
    )
    await query.message.edit_text(text, reply_markup=get_admin_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(query: CallbackQuery):
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) as u_cnt FROM users") as cur:
            users_cnt = (await cur.fetchone())["u_cnt"]
            
        async with db.execute("""
        SELECT 
            COUNT(*) as o_cnt,
            COALESCE(SUM(cost_rub), 0.0) as rev,
            COALESCE(SUM(margin_rub), 0.0) as margin,
            COALESCE(SUM(CASE WHEN type='stars' THEN item_amount ELSE 0 END), 0) as stars
        FROM orders WHERE status = 'completed'
        """) as cur:
            st = await cur.fetchone()
            
    text = (
        f"📊 <b>Финансовая сводка StarVault:</b>\n\n"
        f"👥 Всего пользователей: <b>{users_cnt}</b>\n"
        f"📦 Успешных заказов: <b>{st['o_cnt']}</b>\n"
        f"⭐ Продано звёзд: <b>{st['stars']:,}</b>\n"
        f"💰 Оборот (выручка): <b>{st['rev']:,.2f} ₽</b>\n"
        f"💵 <b>Чистая маржа: {st['margin']:,.2f} ₽</b>"
    )
    await query.message.edit_text(text, reply_markup=get_admin_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "admin_toggle_provider")
async def cb_admin_toggle_provider(query: CallbackQuery):
    curr = await get_setting("active_provider", "mystars")
    next_prov = "greengame" if curr == "mystars" else "mystars"
    await set_setting("active_provider", next_prov)
    await query.answer(f"✅ Провайдер переключен на {next_prov.upper()}", show_alert=True)
    await cb_admin_menu(query, None)

@router.callback_query(F.data == "admin_markup")
async def cb_admin_markup(query: CallbackQuery):
    stars_m = await get_setting("stars_markup_percent", "14.0")
    prem_m = await get_setting("premium_markup_percent", "12.0")
    
    text = (
        f"📈 <b>Управление наценками магазина</b>\n\n"
        f"⭐ Текущая наценка на Stars: <b>+{stars_m}%</b>\n"
        f"👑 Текущая наценка на Premium: <b>+{prem_m}%</b>\n\n"
        f"Выберите, какую наценку хотите изменить:"
    )
    await query.message.edit_text(text, reply_markup=get_admin_markup_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "set_markup_stars")
async def cb_set_markup_stars(query: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsStates.waiting_for_stars_markup)
    await query.message.edit_text("⭐ Введите новый процент наценки на Звёзды (например: 15):", reply_markup=get_cancel_keyboard())

@router.message(StateFilter(AdminSettingsStates.waiting_for_stars_markup))
async def process_stars_markup(message: Message, state: FSMContext):
    try:
        val = float(message.text.strip().replace("%", ""))
        await set_setting("stars_markup_percent", str(val))
        await state.clear()
        await message.answer(f"✅ Наценка на Звёзды установлена: <b>+{val}%</b>", reply_markup=get_admin_keyboard(), parse_mode="HTML")
    except ValueError:
        await message.answer("❌ Введите числовое значение (например: 15):")

@router.callback_query(F.data == "set_markup_premium")
async def cb_set_markup_premium(query: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsStates.waiting_for_premium_markup)
    await query.message.edit_text("👑 Введите новый процент наценки на Premium (например: 12):", reply_markup=get_cancel_keyboard())

@router.message(StateFilter(AdminSettingsStates.waiting_for_premium_markup))
async def process_premium_markup(message: Message, state: FSMContext):
    try:
        val = float(message.text.strip().replace("%", ""))
        await set_setting("premium_markup_percent", str(val))
        await state.clear()
        await message.answer(f"✅ Наценка на Premium установлена: <b>+{val}%</b>", reply_markup=get_admin_keyboard(), parse_mode="HTML")
    except ValueError:
        await message.answer("❌ Введите числовое значение (например: 12):")

# ==============================================================
# 7. ADMIN: TASK CHANNEL CONFIGURATION
# ==============================================================
@router.callback_query(F.data == "admin_task_channel")
async def cb_admin_task_channel(query: CallbackQuery):
    if query.from_user.id not in config.ADMIN_TELEGRAM_IDS:
        await query.answer("❌ Доступ запрещён", show_alert=True)
        return
        
    chan_url = await get_setting("task_channel_url", "https://t.me/starvault_news")
    chan_id = await get_setting("task_channel_id", "@starvault_news")
    reward = await get_setting("task_channel_reward", "50")
    enabled = await get_setting("task_channel_enabled", "1") == "1"
    
    status_text = "🟢 Включено" if enabled else "🔴 Отключено"
    
    text = (
        f"📢 <b>Настройка задания: Подписка на Telegram канал</b>\n\n"
        f"🔗 <b>Ссылка / юзернейм:</b> {chan_url}\n"
        f"🆔 <b>ID / Тег для проверки:</b> <code>{chan_id}</code>\n"
        f"⭐ <b>Награда за подписку:</b> <b>{reward} Stars</b>\n"
        f"🔘 <b>Статус задания:</b> {status_text}\n\n"
        f"<i>При нажатии 'Забрать' сервис проверяет наличие подписки на этот канал.</i>"
    )
    await query.message.edit_text(text, reply_markup=get_admin_task_channel_keyboard(is_enabled=enabled), parse_mode="HTML")

@router.callback_query(F.data == "set_task_chan_url")
async def cb_set_task_chan_url(query: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsStates.waiting_for_channel_url)
    await query.message.edit_text(
        "🔗 <b>Укажите ссылку или @username Telegram канала для задания:</b>\n\n"
        "<i>Примеры:</i>\n"
        "• <code>@starvault_news</code>\n"
        "• <code>https://t.me/starvault_news</code>\n"
        "• <code>https://t.me/+AbCdEfGhIjK</code>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )

@router.message(StateFilter(AdminSettingsStates.waiting_for_channel_url))
async def process_task_chan_url(message: Message, state: FSMContext):
    raw = message.text.strip()
    if not raw:
        await message.answer("❌ Ссылка не может быть пустой:")
        return
        
    if raw.startswith("@"):
        chan_url = f"https://t.me/{raw.lstrip('@')}"
        chan_id = raw
    elif raw.startswith("https://t.me/"):
        chan_url = raw
        extracted = raw.replace("https://t.me/", "").split("/")[0].split("?")[0]
        chan_id = f"@{extracted}" if not extracted.startswith("+") else extracted
    else:
        chan_url = f"https://t.me/{raw.lstrip('@')}"
        chan_id = f"@{raw.lstrip('@')}"
        
    await set_setting("task_channel_url", chan_url)
    await set_setting("task_channel_id", chan_id)
    await state.clear()
    await message.answer(
        f"✅ <b>Канал для задания успешно обновлён!</b>\n\n"
        f"🔗 Ссылка: {chan_url}\n"
        f"🆔 ID / Username: <code>{chan_id}</code>",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "set_task_chan_reward")
async def cb_set_task_chan_reward(query: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsStates.waiting_for_channel_reward)
    await query.message.edit_text(
        "⭐ <b>Введите количество Звёзд (Stars) в награду за подписку на канал:</b>\n\n"
        "<i>Например: 50 или 100</i>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )

@router.message(StateFilter(AdminSettingsStates.waiting_for_channel_reward))
async def process_task_chan_reward(message: Message, state: FSMContext):
    try:
        val = int(message.text.strip())
        if val <= 0 or val > 10000:
            await message.answer("❌ Награда должна быть от 1 до 10 000 Звёзд:")
            return
        await set_setting("task_channel_reward", str(val))
        await state.clear()
        await message.answer(
            f"✅ <b>Награда за подписку на канал установлена: {val} ⭐ Stars</b>",
            reply_markup=get_admin_keyboard(),
            parse_mode="HTML"
        )
    except ValueError:
        await message.answer("❌ Введите целое число (например: 50):")

@router.callback_query(F.data == "toggle_task_chan_status")
async def cb_toggle_task_chan_status(query: CallbackQuery):
    curr = await get_setting("task_channel_enabled", "1")
    new_st = "0" if curr == "1" else "1"
    await set_setting("task_channel_enabled", new_st)
    status_str = "🟢 Включено" if new_st == "1" else "🔴 Отключено"
    await query.answer(f"Задание подписки: {status_str}", show_alert=True)
    await cb_admin_task_channel(query)

# ==============================================================
# 8. ADMIN: ORDERS & USERS & BROADCAST
# ==============================================================
@router.callback_query(F.data == "admin_orders")
async def cb_admin_orders(query: CallbackQuery):
    async with get_db() as db:
        async with db.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 5") as cur:
            orders = [dict(r) for r in await cur.fetchall()]
            
    if not orders:
        await query.message.edit_text("📦 Заказов пока нет.", reply_markup=get_admin_keyboard())
        return
        
    buttons = []
    for o in orders:
        buttons.append([InlineKeyboardButton(text=f"{o['order_uid']} • @{o['recipient_username']} ({o['status']})", callback_data=f"view_ord:{o['order_uid']}")])
    buttons.append([InlineKeyboardButton(text="🔙 В админку", callback_data="menu_admin")])
    
    await query.message.edit_text("📦 <b>Последние заказы:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("view_ord:"))
async def cb_view_order(query: CallbackQuery):
    order_uid = query.data.split(":")[1]
    async with get_db() as db:
        async with db.execute("SELECT * FROM orders WHERE order_uid = ?", (order_uid,)) as cur:
            order = await cur.fetchone()
            
    if not order:
        await query.answer("Заказ не найден", show_alert=True)
        return
        
    text = (
        f"📦 <b>Заказ {order['order_uid']}</b>\n\n"
        f"👤 Получатель: <b>@{order['recipient_username']}</b>\n"
        f"🎁 Товар: <b>{order['item_amount']} {order['type']}</b>\n"
        f"💰 Сумма: <b>{order['cost_rub']:.2f} ₽</b>\n"
        f"💵 Маржа: <b>+{order['margin_rub']:.2f} ₽</b>\n"
        f"📡 Шлюз: <b>{order['provider']}</b>\n"
        f"📊 Статус: <b>{order['status'].upper()}</b>\n"
        f"🔑 ID провайдера: <code>{order['provider_order_id'] or '—'}</code>"
    )
    await query.message.edit_text(text, reply_markup=get_order_action_keyboard(order_uid), parse_mode="HTML")

@router.callback_query(F.data.startswith("ord_act:"))
async def cb_order_action(query: CallbackQuery):
    parts = query.data.split(":")
    action = parts[1]
    order_uid = parts[2]
    
    async with get_db() as db:
        async with db.execute("SELECT * FROM orders WHERE order_uid = ?", (order_uid,)) as cur:
            order = await cur.fetchone()
            if not order:
                await query.answer("Заказ не найден", show_alert=True)
                return
                
        if action == "complete":
            await db.execute("UPDATE orders SET status = 'completed', updated_at = CURRENT_TIMESTAMP WHERE order_uid = ?", (order_uid,))
            await db.commit()
            await query.answer("✅ Заказ отмечен как выполненный!", show_alert=True)
        elif action == "refund":
            await db.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (order["cost_rub"], order["user_id"]))
            await db.execute("UPDATE orders SET status = 'refunded', updated_at = CURRENT_TIMESTAMP WHERE order_uid = ?", (order_uid,))
            await db.commit()
            await query.answer(f"↩️ {order['cost_rub']} ₽ возвращено клиенту на баланс!", show_alert=True)
        elif action == "retry":
            res = await ProviderService.deliver_order(order["order_uid"], order["type"], order["item_amount"], order["recipient_username"])
            if res.get("success"):
                await db.execute("UPDATE orders SET status = 'completed', provider_order_id = ? WHERE order_uid = ?", (res.get("provider_order_id"), order_uid))
                await db.commit()
                await query.answer("🚀 Повторная отправка успешна!", show_alert=True)
            else:
                await query.answer("❌ Ошибка доставки", show_alert=True)
                
    await cb_admin_orders(query)

@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(query: CallbackQuery, state: FSMContext):
    await state.set_state(AdminBroadcastStates.waiting_for_message)
    await query.message.edit_text(
        "📢 <b>Рассылка сообщений</b>\n\nОтправьте текст или фото с подписью для массовой рассылки по всей базе пользователей бота:",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )

@router.message(StateFilter(AdminBroadcastStates.waiting_for_message))
async def process_broadcast_message(message: Message, state: FSMContext):
    async with get_db() as db:
        async with db.execute("SELECT telegram_id FROM users WHERE is_banned = 0") as cur:
            users = await cur.fetchall()
            
    sent_count = 0
    failed_count = 0
    status_msg = await message.answer(f"🚀 Запуск рассылки на {len(users)} пользователей...")
    
    for u in users:
        tg_id = u["telegram_id"]
        try:
            if message.photo:
                await message.bot.send_photo(tg_id, photo=message.photo[-1].file_id, caption=message.caption, parse_mode="HTML")
            else:
                await message.bot.send_message(tg_id, text=message.text, parse_mode="HTML")
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed_count += 1
            
    await state.clear()
    await status_msg.edit_text(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"✉️ Успешно доставлено: <b>{sent_count}</b>\n"
        f"⚠️ Ошибок (заблокировали бота): <b>{failed_count}</b>",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "admin_maintenance")
async def cb_admin_maintenance(query: CallbackQuery):
    curr = await get_setting("site_maintenance", "0")
    new_st = "1" if curr == "0" else "0"
    await set_setting("site_maintenance", new_st)
    status_str = "ВКЛЮЧЕН (Магазин закрыт на техработы)" if new_st == "1" else "ВЫКЛЮЧЕН (Магазин работает штатно)"
    await query.answer(f"Режим техработ: {status_str}", show_alert=True)
    await cb_admin_menu(query, None)
