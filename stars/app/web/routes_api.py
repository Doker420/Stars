import uuid
import json
import logging
from fastapi import APIRouter, HTTPException, Query, Body, Request, Header
from pydantic import BaseModel, Field
from app.config import config
from app.database import get_db
from app.services.pricing import calculate_stars_price, calculate_premium_price, get_setting
from app.services.payments import PaymentService
from app.services.referrals import PromoService
from app.services.security import check_anti_flood, verify_telegram_init_data
from app.services.providers import ProviderService
from app.services.mystars_client import MyStarsClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["Public API"])

async def fetch_telegram_avatar(telegram_id: int) -> str | None:
    if not config.BOT_TOKEN or config.BOT_TOKEN.startswith("7777777777") or config.BOT_TOKEN.startswith("1234567890"):
        return None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"https://api.telegram.org/bot{config.BOT_TOKEN}/getUserProfilePhotos",
                params={"user_id": telegram_id, "limit": 1}
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok") and data.get("result", {}).get("total_count", 0) > 0:
                    photos = data["result"]["photos"]
                    if photos and len(photos[0]) > 0:
                        file_id = photos[0][-1]["file_id"]
                        file_resp = await client.get(
                            f"https://api.telegram.org/bot{config.BOT_TOKEN}/getFile",
                            params={"file_id": file_id}
                        )
                        if file_resp.status_code == 200:
                            file_data = file_resp.json()
                            if file_data.get("ok"):
                                file_path = file_data["result"]["file_path"]
                                return f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{file_path}"
    except Exception as e:
        logger.debug(f"Could not fetch telegram avatar for {telegram_id}: {e}")
    return None

async def get_user_by_id_or_tg(db, user_id_val: int | str, auto_create_tg: bool = True, username: str = None, first_name: str = None) -> dict | None:
    try:
        uid = int(str(user_id_val).strip())
    except (ValueError, TypeError):
        return None
        
    async with db.execute("SELECT * FROM users WHERE id = ? OR telegram_id = ?", (uid, uid)) as cur:
        row = await cur.fetchone()
        if row:
            return dict(row)
            
    if auto_create_tg and uid > 0:
        clean_username = (username or f"user_{uid}").lstrip("@")
        clean_first_name = first_name or clean_username
        ref_code = f"U{uuid.uuid4().hex[:6].upper()}"
        tg_id = uid
        
        await db.execute("""
        INSERT OR IGNORE INTO users (telegram_id, username, first_name, balance, stars_balance, spins_count, ref_code, is_admin)
        VALUES (?, ?, ?, 0.0, 0.0, 1, ?, 0)
        """, (tg_id, clean_username, clean_first_name, ref_code))
        await db.commit()
        
        async with db.execute("SELECT * FROM users WHERE telegram_id = ? OR id = ?", (tg_id, uid)) as cur:
            row = await cur.fetchone()
            if row:
                return dict(row)
    return None

class TelegramAuthRequest(BaseModel):
    telegram_id: int | str
    username: str | None = None
    first_name: str | None = None
    avatar_url: str | None = None
    referrer_id: int | str | None = None
    init_data: str | None = None
    is_premium: bool | None = None

class CalculateRequest(BaseModel):
    type: str # 'stars' or 'premium'
    amount: int # stars count or months (3, 6, 12)
    promo_code: str | None = None

class CreateOrderRequest(BaseModel):
    user_id: int | str
    type: str
    amount: int
    recipient_username: str
    payment_method: str # cryptobot, yookassa, yoomoney, balance, stars_wallet
    promo_code: str | None = None

class TopupBalanceRequest(BaseModel):
    user_id: int | str
    amount_rub: float
    payment_method: str # yookassa, yoomoney, cryptobot

class RedeemPromoRequest(BaseModel):
    user_id: int | str
    code: str

class RecipientCheckRequest(BaseModel):
    type: str = "stars" # 'stars' or 'premium'
    username: str

@router.post("/user/auth-telegram")
async def auth_telegram_user(req: TelegramAuthRequest):
    """
    Authenticates or creates a user seamlessly when Telegram WebApp opens.
    Returns real user state from database.
    """
    try:
        tg_id = int(str(req.telegram_id).strip())
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid telegram_id")

    clean_username = (req.username or f"user_{tg_id}").lstrip("@")
    clean_first_name = req.first_name or clean_username
    
    # Try resolving avatar if not provided
    avatar = req.avatar_url
    if not avatar:
        avatar = await fetch_telegram_avatar(tg_id)
    
    # Parse referrer if provided
    ref_by = None
    if req.referrer_id:
        try:
            ref_str = str(req.referrer_id).replace("ref_", "").strip()
            ref_int = int(ref_str)
            if ref_int != tg_id:
                ref_by = ref_int
        except Exception:
            pass

    async with get_db() as db:
        ref_code = f"U{uuid.uuid4().hex[:6].upper()}"
        
        await db.execute("""
        INSERT OR IGNORE INTO users (telegram_id, username, first_name, balance, stars_balance, spins_count, avatar_url, ref_code, referred_by, is_admin)
        VALUES (?, ?, ?, 0.0, 0.0, 1, ?, ?, ?, 0)
        """, (tg_id, clean_username, clean_first_name, avatar, ref_code, ref_by))
        
        await db.execute("""
        UPDATE users SET username = ?, first_name = ?, avatar_url = COALESCE(?, avatar_url), telegram_is_premium = COALESCE(?, telegram_is_premium)
        WHERE telegram_id = ?
        """, (clean_username, clean_first_name, avatar, int(req.is_premium) if req.is_premium is not None else None, tg_id))
        await db.commit()
        
        async with db.execute("SELECT id FROM users WHERE telegram_id = ?", (tg_id,)) as cur:
            user_row = await cur.fetchone()
            if not user_row:
                raise HTTPException(status_code=500, detail="Could not create/find user")
            user_id = user_row["id"]
                
    return await get_user_profile(user_id)

@router.post("/faas/recipient-check")
async def check_recipient_faas(req: RecipientCheckRequest):
    """
    Direct MyStars FaaS recipient check probe (https://mystars.tg/docs)
    """
    return await MyStarsClient.check_recipient(req.username, order_type=req.type)

@router.get("/faas/products")
async def get_faas_products():
    """
    Direct MyStars FaaS product catalog (https://mystars.tg/docs)
    """
    return await MyStarsClient.get_products()

@router.get("/catalog")
async def get_catalog():
    maintenance = await get_setting("site_maintenance", "0") == "1"
    announcement = await get_setting("site_announcement", "")
    theme = await get_setting("site_theme", "telegram_dark")
    active_provider = await get_setting("active_provider", "mystars")
    payment_methods = await PaymentService.get_enabled_methods()
    
    # Precompute standard Star packs
    star_packs = [50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000]
    stars_pricing = []
    for count in star_packs:
        pricing = await calculate_stars_price(count)
        stars_pricing.append({
            "amount": count,
            "cost_rub": pricing["total_rub"],
            "cost_usdt": pricing["total_usdt"],
            "cost_ton": pricing["total_ton"],
            "unit_price_rub": pricing["unit_price_rub"]
        })
        
    # Precompute Premium plans
    premium_plans = []
    for m in [3, 6, 12]:
        p_calc = await calculate_premium_price(m)
        premium_plans.append({
            "months": m,
            "cost_rub": p_calc["total_rub"],
            "cost_usdt": p_calc["total_usdt"],
            "cost_ton": p_calc["total_ton"],
            "base_cost_rub": p_calc["base_cost_rub"],
            "popular": m == 3,
            "best_deal": m == 12
        })
        
    return {
        "status": "active" if not maintenance else "maintenance",
        "maintenance": maintenance,
        "announcement": announcement,
        "theme": theme,
        "active_provider": active_provider,
        "payment_methods": payment_methods,
        "stars_packs": stars_pricing,
        "premium_plans": premium_plans,
        "stars_limits": {"min": 50, "max": 1000000}
    }

@router.post("/calculate")
async def calculate_order(req: CalculateRequest):
    discount = 0.0
    if req.promo_code:
        val = await PromoService.validate_promo(req.promo_code)
        if val.get("valid") and val.get("type") == "discount":
            discount = val.get("value", 0.0)
            
    if req.type == "stars":
        amount = max(50, min(1000000, req.amount))
        res = await calculate_stars_price(amount, discount_percent=discount)
    elif req.type == "premium":
        months = req.amount if req.amount in [3, 6, 12] else 3
        res = await calculate_premium_price(months, discount_percent=discount)
    else:
        raise HTTPException(status_code=400, detail="Invalid item type")
        
    return res

@router.post("/order/create")
async def create_order(req: CreateOrderRequest):
    if not check_anti_flood(str(req.user_id)):
        raise HTTPException(status_code=429, detail="Слишком много запросов. Подождите немного.")
        
    clean_username = req.recipient_username.lstrip("@").strip()
    if not clean_username or len(clean_username) < 3:
        raise HTTPException(status_code=400, detail="Укажите корректный Telegram @username получателя")
        
    # Recipient eligibility check
    recipient_probe = await ProviderService.check_recipient(clean_username, order_type=req.type)
    if not recipient_probe.get("eligible", True) and not recipient_probe.get("indeterminate", False):
        msg = recipient_probe.get("telegram_message") or "Указанный получатель не может принять этот товар"
        if recipient_probe.get("reason") == "already_subscribed":
            msg = f"❌ Пользователь @{clean_username} уже имеет активную подписку Telegram Premium."
        raise HTTPException(status_code=422, detail=msg)

    async with get_db() as db:
        user = await get_user_by_id_or_tg(db, req.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        if user["is_banned"]:
            raise HTTPException(status_code=403, detail="Ваш аккаунт заблокирован")
        actual_user_id = user["id"]

    discount = 0.0
    if req.promo_code:
        val = await PromoService.validate_promo(req.promo_code, actual_user_id)
        if val.get("valid") and val.get("type") == "discount":
            discount = val.get("value", 0.0)
            
    if req.type == "stars":
        amount = max(50, min(1000000, req.amount))
        calc = await calculate_stars_price(amount, discount_percent=discount)
    elif req.type == "premium":
        months = req.amount if req.amount in [3, 6, 12] else 3
        preset = {3: 1056.0, 6: 1410.0, 12: 2554.0}
        base_rub = preset.get(months, 1056.0)
        final_rub = round(base_rub * (1.0 - discount / 100.0), 2) if discount > 0 else base_rub
        usdt_rub_rate = float(await get_setting("usdt_rub_rate", "92.5"))
        ton_usdt_rate = float(await get_setting("ton_usdt_rate", "3.20"))
        calc = {
            "total_rub": final_rub,
            "total_usdt": round(final_rub / usdt_rub_rate, 2),
            "total_ton": round(final_rub / usdt_rub_rate / ton_usdt_rate, 2),
            "base_cost_rub": round(final_rub * 0.85, 2),
            "margin_rub": round(final_rub * 0.15, 2)
        }
    else:
        raise HTTPException(status_code=400, detail="Неверный тип товара")
        
    order_uid = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    active_provider = await get_setting("active_provider", "mystars")
    
    async with get_db() as db:
        await db.execute("""
        INSERT INTO orders (
            order_uid, user_id, type, item_amount, recipient_username,
            cost_rub, cost_usdt, cost_ton, base_cost_rub, margin_rub,
            provider, status, payment_method
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (
            order_uid, actual_user_id, req.type, req.amount, clean_username,
            calc["total_rub"], calc["total_usdt"], calc["total_ton"], calc["base_cost_rub"], calc["margin_rub"],
            active_provider, req.payment_method
        ))
        await db.commit()
        
    payment_info = await PaymentService.create_payment(
        order_id=order_uid,
        amount_rub=calc["total_rub"],
        amount_usdt=calc["total_usdt"],
        method=req.payment_method,
        user_id=actual_user_id,
        description=f"Покупка {req.amount} {'Stars' if req.type == 'stars' else 'мес. Premium'} для @{clean_username}"
    )
    
    if not payment_info.get("success"):
        async with get_db() as db:
            await db.execute("DELETE FROM orders WHERE order_uid = ?", (order_uid,))
            await db.commit()
        raise HTTPException(status_code=400, detail=payment_info.get("error", "Ошибка при создании платежа"))
        
    return {
        "success": True,
        "order_uid": order_uid,
        "type": req.type,
        "amount": req.amount,
        "recipient_username": clean_username,
        "cost_rub": calc["total_rub"],
        "cost_usdt": calc["total_usdt"],
        "cost_ton": calc["total_ton"],
        "payment": payment_info
    }

@router.post("/balance/topup")
async def topup_balance(req: TopupBalanceRequest):
    """
    Creates a real deposit payment through YooMoney, CryptoBot, or YooKassa.
    """
    if req.amount_rub < 50 or req.amount_rub > 100000:
        raise HTTPException(status_code=400, detail="Сумма пополнения должна быть от 50 до 100 000 ₽")
        
    async with get_db() as db:
        user = await get_user_by_id_or_tg(db, req.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        if user["is_banned"]:
            raise HTTPException(status_code=403, detail="Пользователь заблокирован")
        actual_user_id = user["id"]
        
    res = await PaymentService.create_deposit_invoice(
        user_id=actual_user_id,
        amount_rub=req.amount_rub,
        method=req.payment_method
    )
    
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Не удалось создать платёж"))
        
    return res

@router.get("/balance/check/{deposit_id}")
async def check_deposit_status(deposit_id: str):
    """
    Checks if a deposit has been settled via webhook or gateway.
    """
    st = await PaymentService.check_payment_status("deposit", deposit_id)
    return st

@router.get("/order/{order_uid}")
async def get_order_status(order_uid: str):
    async with get_db() as db:
        async with db.execute("SELECT * FROM orders WHERE order_uid = ?", (order_uid,)) as cur:
            order = await cur.fetchone()
            if not order:
                raise HTTPException(status_code=404, detail="Заказ не найден")
            return dict(order)

@router.get("/user/me")
async def get_user_profile(user_id: int | str = Query(...)):
    async with get_db() as db:
        user = await get_user_by_id_or_tg(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
            
        actual_user_id = user["id"]
        actual_tg_id = user["telegram_id"]
                
        async with db.execute("SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 20", (actual_user_id,)) as cur:
            orders = [dict(row) for row in await cur.fetchall()]
            
        async with db.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 20", (actual_user_id,)) as cur:
            txs = [dict(row) for row in await cur.fetchall()]
            
        async with db.execute("SELECT COUNT(*) as count FROM users WHERE referred_by = ?", (actual_tg_id,)) as cur:
            ref_count = (await cur.fetchone())["count"]
            
        async with db.execute("""
        SELECT COALESCE(SUM(amount_rub), 0.0) as earned 
        FROM transactions 
        WHERE user_id = ? AND type = 'ref_reward'
        """, (actual_user_id,)) as cur:
            ref_earned = (await cur.fetchone())["earned"]
            
        ref_default = float(await get_setting("ref_default_percent", "5.0"))
        active_ref_percent = user["custom_ref_percent"] if user.get("custom_ref_percent") is not None else ref_default
        
        return {
            "user": {
                "id": user["id"],
                "telegram_id": user["telegram_id"],
                "username": user["username"],
                "first_name": user["first_name"],
                "balance": user["balance"],
                "stars_balance": user.get("stars_balance", 0.0),
                "spins_count": user.get("spins_count", 1),
                "avatar_url": user.get("avatar_url"),
                "ref_code": user["ref_code"],
                "ref_percent": active_ref_percent,
                "is_admin": bool(user["is_admin"])
            },
            "referrals": {
                "count": ref_count,
                "earned_rub": round(ref_earned, 2),
                "ref_link": f"https://t.me/StarVaultRoBot?start=ref_{user['telegram_id']}"
            },
            "orders": orders,
            "transactions": txs
        }

@router.post("/user/promo/redeem")
async def redeem_promo(req: RedeemPromoRequest):
    async with get_db() as db:
        user = await get_user_by_id_or_tg(db, req.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        actual_user_id = user["id"]
        
    res = await PromoService.apply_promo(req.code, actual_user_id)
    return res
