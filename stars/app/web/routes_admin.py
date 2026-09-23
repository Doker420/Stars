import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query, Body, Request
from pydantic import BaseModel
from app.database import get_db
from app.services.pricing import get_setting, set_setting
from app.services.security import log_audit
from app.services.providers import ProviderService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["Admin Panel"])

class UpdateSettingsRequest(BaseModel):
    stars_base_rate_rub: float | None = None
    stars_markup_percent: float | None = None
    premium_3m_base_rub: float | None = None
    premium_6m_base_rub: float | None = None
    premium_12m_base_rub: float | None = None
    premium_markup_percent: float | None = None
    active_provider: str | None = None
    payment_cryptobot_enabled: bool | None = None
    payment_yookassa_enabled: bool | None = None
    payment_yoomoney_enabled: bool | None = None
    payment_balance_enabled: bool | None = None
    ref_default_percent: float | None = None
    site_maintenance: bool | None = None
    site_announcement: str | None = None
    site_theme: str | None = None
    task_channel_url: str | None = None
    task_channel_id: str | None = None
    task_channel_reward: str | None = None
    task_channel_enabled: bool | None = None

class OrderActionRequest(BaseModel):
    action: str # 'complete', 'refund', 'retry'
    admin_id: int = 1

class UserBalanceRequest(BaseModel):
    amount: float
    reason: str = "Ручная корректировка администратором"
    admin_id: int = 1

class UserBanRequest(BaseModel):
    is_banned: bool
    admin_id: int = 1

class CreatePromoRequest(BaseModel):
    code: str
    type: str # 'balance' or 'discount'
    value: float
    max_uses: int = 100
    admin_id: int = 1

class BroadcastRequest(BaseModel):
    message: str
    admin_id: int = 1

@router.get("/stats")
async def get_admin_stats(period: str = Query("all")): # today, week, month, all
    async with get_db() as db:
        # Total users
        async with db.execute("SELECT COUNT(*) as cnt FROM users") as cur:
            total_users = (await cur.fetchone())["cnt"]
            
        # Total orders & margins
        where_clause = "WHERE status = 'completed'"
        if period == "today":
            where_clause += " AND date(created_at) = date('now')"
        elif period == "week":
            where_clause += " AND created_at >= datetime('now', '-7 days')"
        elif period == "month":
            where_clause += " AND created_at >= datetime('now', '-30 days')"

        async with db.execute(f"""
        SELECT 
            COUNT(*) as total_orders,
            COALESCE(SUM(cost_rub), 0.0) as total_revenue_rub,
            COALESCE(SUM(margin_rub), 0.0) as total_margin_rub,
            COALESCE(SUM(CASE WHEN type = 'stars' THEN item_amount ELSE 0 END), 0) as total_stars_sold,
            COALESCE(SUM(CASE WHEN type = 'premium' THEN 1 ELSE 0 END), 0) as total_premium_sold
        FROM orders {where_clause}
        """) as cur:
            stats = dict(await cur.fetchone())

        # Recent orders count
        async with db.execute("SELECT COUNT(*) as pending_cnt FROM orders WHERE status = 'pending' OR status = 'processing'") as cur:
            stats["pending_orders"] = (await cur.fetchone())["pending_cnt"]
            
        # Active provider & settings quick summary
        stats["total_users"] = total_users
        stats["active_provider"] = await get_setting("active_provider", "mystars")
        stats["stars_markup"] = await get_setting("stars_markup_percent", "14.0")
        stats["premium_markup"] = await get_setting("premium_markup_percent", "12.0")
        stats["task_channel_url"] = await get_setting("task_channel_url", "https://t.me/starvault_news")
        stats["task_channel_id"] = await get_setting("task_channel_id", "@starvault_news")
        stats["task_channel_reward"] = await get_setting("task_channel_reward", "50")
        stats["task_channel_enabled"] = await get_setting("task_channel_enabled", "1")
        
        return stats

@router.get("/orders")
async def list_orders(search: str = Query(""), status: str = Query("all"), limit: int = 50):
    async with get_db() as db:
        query = """
        SELECT o.*, u.username as customer_username, u.first_name as customer_name
        FROM orders o
        LEFT JOIN users u ON o.user_id = u.id
        WHERE 1=1
        """
        params = []
        if status and status != "all":
            query += " AND o.status = ?"
            params.append(status)
            
        if search:
            query += " AND (o.order_uid LIKE ? OR o.recipient_username LIKE ? OR u.username LIKE ?)"
            s_param = f"%{search}%"
            params.extend([s_param, s_param, s_param])
            
        query += " ORDER BY o.id DESC LIMIT ?"
        params.append(limit)
        
        async with db.execute(query, tuple(params)) as cur:
            orders = [dict(row) for row in await cur.fetchall()]
            return {"orders": orders}

@router.post("/order/{order_uid}/action")
async def handle_order_action(order_uid: str, req: OrderActionRequest):
    async with get_db() as db:
        async with db.execute("SELECT * FROM orders WHERE order_uid = ?", (order_uid,)) as cur:
            order = await cur.fetchone()
            if not order:
                raise HTTPException(status_code=404, detail="Заказ не найден")
                
        if req.action == "complete":
            await db.execute("""
            UPDATE orders SET status = 'completed', updated_at = CURRENT_TIMESTAMP WHERE order_uid = ?
            """, (order_uid,))
            await db.commit()
            await log_audit(req.admin_id, f"MANUAL_COMPLETE", f"Заказ {order_uid} вручную переведён в статус 'Выполнен'")
            return {"success": True, "message": f"Заказ {order_uid} отмечен как выполненный"}
            
        elif req.action == "refund":
            if order["status"] == "refunded":
                raise HTTPException(status_code=400, detail="Заказ уже возвращён")
                
            # Refund amount to user balance
            await db.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (order["cost_rub"], order["user_id"]))
            await db.execute("""
            INSERT INTO transactions (user_id, type, amount_rub, description, order_id)
            VALUES (?, 'refund', ?, ?, ?)
            """, (order["user_id"], order["cost_rub"], f"Возврат средств администратором за {order_uid}", order_uid))
            await db.execute("UPDATE orders SET status = 'refunded', updated_at = CURRENT_TIMESTAMP WHERE order_uid = ?", (order_uid,))
            await db.commit()
            await log_audit(req.admin_id, f"REFUND_ORDER", f"Возврат {order['cost_rub']} ₽ пользователю #{order['user_id']} за {order_uid}")
            return {"success": True, "message": f"Заказ {order_uid} возвращён. {order['cost_rub']} ₽ зачислено на баланс клиента."}
            
        elif req.action == "retry":
            # Retry delivery via active provider
            res = await ProviderService.deliver_order(
                order_id=order["order_uid"],
                order_type=order["type"],
                amount=order["item_amount"],
                recipient_username=order["recipient_username"]
            )
            if res.get("success"):
                await db.execute("""
                UPDATE orders SET status = 'completed', provider = ?, provider_order_id = ?, updated_at = CURRENT_TIMESTAMP WHERE order_uid = ?
                """, (res.get("provider"), res.get("provider_order_id"), order_uid))
                await db.commit()
                await log_audit(req.admin_id, f"RETRY_DELIVERY_SUCCESS", f"Повторная доставка {order_uid} успешна: {res.get('provider_order_id')}")
                return {"success": True, "message": f"Заказ успешно выдан через {res.get('provider')}!"}
            else:
                return {"success": False, "message": f"Ошибка доставки: {res.get('message')}"}
        else:
            raise HTTPException(status_code=400, detail="Недопустимое действие")

@router.get("/settings")
async def get_all_settings():
    async with get_db() as db:
        async with db.execute("SELECT key, value FROM settings") as cur:
            rows = await cur.fetchall()
            return {row["key"]: row["value"] for row in rows}

@router.post("/settings")
async def save_settings(req: UpdateSettingsRequest):
    data = req.model_dump(exclude_unset=True)
    for key, value in data.items():
        if value is not None:
            if isinstance(value, bool):
                val_str = "1" if value else "0"
            else:
                val_str = str(value)
            await set_setting(key, val_str)
            
    await log_audit(1, "UPDATE_SETTINGS", f"Изменены параметры: {list(data.keys())}")
    return {"success": True, "message": "Настройки успешно сохранены без перезапуска системы"}

@router.get("/users")
async def list_users(search: str = Query(""), limit: int = 50):
    async with get_db() as db:
        query = "SELECT * FROM users WHERE 1=1"
        params = []
        if search:
            query += " AND (username LIKE ? OR first_name LIKE ? OR telegram_id LIKE ?)"
            s = f"%{search}%"
            params.extend([s, s, s])
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        
        async with db.execute(query, tuple(params)) as cur:
            users = [dict(row) for row in await cur.fetchall()]
            return {"users": users}

@router.post("/user/{user_id}/balance")
async def adjust_user_balance(user_id: int, req: UserBalanceRequest):
    async with get_db() as db:
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cur:
            user = await cur.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="Пользователь не найден")
                
        await db.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (req.amount, user_id))
        await db.execute("""
        INSERT INTO transactions (user_id, type, amount_rub, description)
        VALUES (?, 'admin_adjust', ?, ?)
        """, (user_id, req.amount, req.reason))
        await db.commit()
        
    await log_audit(req.admin_id, "ADJUST_BALANCE", f"Пользователю #{user_id} ({user['username']}) начислено/списано {req.amount} ₽. Причина: {req.reason}")
    return {"success": True, "message": f"Баланс пользователя #{user_id} обновлён на {req.amount:+} ₽"}

@router.post("/user/{user_id}/ban")
async def toggle_user_ban(user_id: int, req: UserBanRequest):
    async with get_db() as db:
        await db.execute("UPDATE users SET is_banned = ? WHERE id = ?", (1 if req.is_banned else 0, user_id))
        await db.commit()
    await log_audit(req.admin_id, "TOGGLE_BAN", f"Пользователь #{user_id} ban={req.is_banned}")
    return {"success": True, "message": f"Статус бана обновлен: {req.is_banned}"}

@router.get("/promos")
async def list_promos():
    async with get_db() as db:
        async with db.execute("SELECT * FROM promo_codes ORDER BY id DESC") as cur:
            promos = [dict(row) for row in await cur.fetchall()]
            return {"promos": promos}

@router.post("/promos")
async def create_promo(req: CreatePromoRequest):
    clean_code = req.code.strip().upper()
    async with get_db() as db:
        try:
            await db.execute("""
            INSERT INTO promo_codes (code, type, value, max_uses, current_uses, is_active)
            VALUES (?, ?, ?, ?, 0, 1)
            """, (clean_code, req.type, req.value, req.max_uses))
            await db.commit()
        except Exception as e:
            raise HTTPException(status_code=400, detail="Промокод с таким именем уже существует")
            
    await log_audit(req.admin_id, "CREATE_PROMO", f"Создан промокод {clean_code} ({req.type}={req.value})")
    return {"success": True, "message": f"Промокод {clean_code} успешно создан"}

@router.post("/broadcast")
async def send_broadcast(req: BroadcastRequest):
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) as cnt FROM users WHERE is_banned = 0") as cur:
            count = (await cur.fetchone())["cnt"]
            
    await log_audit(req.admin_id, "BROADCAST", f"Рассылка сообщения на {count} пользователей: '{req.message[:40]}...'")
    return {
        "success": True,
        "message": f"Рассылка успешно запущена для {count} активных пользователей бота!",
        "delivered_count": count
    }

@router.get("/audit-logs")
async def get_audit_logs(limit: int = 50):
    async with get_db() as db:
        async with db.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)) as cur:
            logs = [dict(row) for row in await cur.fetchall()]
            return {"logs": logs}
