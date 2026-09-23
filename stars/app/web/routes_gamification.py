import json
import random
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Query, Body, Request
from pydantic import BaseModel
from app.database import get_db
from app.services.pricing import get_setting
from app.services.tasks import discover_external_tasks, is_channel_member

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["Gamification: Wheel & Tasks"])

SECTORS = [
    {"index": 0, "type": "stars", "value": 25, "label": "25 ⭐", "title": "+25 Stars", "weight": 20},
    {"index": 1, "type": "rub", "value": 50.0, "label": "50 ₽", "title": "+50 ₽ на баланс", "weight": 15},
    {"index": 2, "type": "stars", "value": 10, "label": "10 ⭐", "title": "+10 Stars", "weight": 25},
    {"index": 3, "type": "discount", "value": 10.0, "label": "-10%", "title": "Скидка 10%", "weight": 15},
    {"index": 4, "type": "stars", "value": 100, "label": "100 ⭐", "title": "🌟 ДЖЕКПОТ! +100 Stars", "weight": 5},
    {"index": 5, "type": "spin", "value": 1, "label": "+1 Спин", "title": "🔄 +1 Доп. Спин", "weight": 10},
    {"index": 6, "type": "stars", "value": 5, "label": "5 ⭐", "title": "+5 Stars", "weight": 25},
    {"index": 7, "type": "rub", "value": 100.0, "label": "100 ₽", "title": "+100 ₽ на баланс", "weight": 5},
]

async def get_user_by_id_or_tg(db, user_id_val: int | str) -> dict | None:
    try:
        uid = int(user_id_val)
    except (ValueError, TypeError):
        return None
        
    async with db.execute("SELECT * FROM users WHERE id = ? OR telegram_id = ?", (uid, uid)) as cur:
        row = await cur.fetchone()
        if row:
            return dict(row)
            
    if uid > 0:
        tg_id = uid
        ref_code = f"U{uuid.uuid4().hex[:6].upper()}"
        await db.execute("""
        INSERT OR IGNORE INTO users (telegram_id, username, first_name, balance, stars_balance, spins_count, ref_code, is_admin)
        VALUES (?, ?, 'Пользователь', 0.0, 0.0, 1, ?, 0)
        """, (tg_id, f"user_{tg_id}", ref_code))
        await db.commit()
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (tg_id,)) as cur:
            row = await cur.fetchone()
            if row:
                return dict(row)
    return None

class SpinWheelRequest(BaseModel):
    user_id: int | str

class ClaimTaskRequest(BaseModel):
    user_id: int | str

class OpenCaseRequest(BaseModel):
    user_id: int | str
    payment_method: str = "key"

CASES = {
    "starter": {
        "id": "starter", "title": "Стартовый сундук", "icon": "🎁",
        "prices": {"key": 1, "stars": 20, "rub": 25},
        "rewards": [
            ("stars", 5, 35), ("stars", 10, 28), ("stars", 25, 12),
            ("rub", 10, 10), ("spin", 1, 10), ("key", 1, 5),
        ],
    },
    "premium": {
        "id": "premium", "title": "Золотой сундук", "icon": "👑",
        "prices": {"key": 3, "stars": 75, "rub": 99},
        "rewards": [
            ("stars", 25, 30), ("stars", 50, 25), ("stars", 100, 8),
            ("rub", 50, 15), ("spin", 2, 12), ("key", 2, 10),
        ],
    },
}

LEVELS = ((0, "Новичок"), (100, "Активный"), (300, "Продвинутый"), (700, "Эксперт"), (1500, "VIP"))


def level_info(xp: int) -> dict:
    current = LEVELS[0]
    next_level = None
    for item in LEVELS:
        if xp >= item[0]:
            current = item
        elif next_level is None:
            next_level = item
    return {
        "name": current[1], "xp": xp,
        "next_xp": next_level[0] if next_level else None,
        "progress": 100 if not next_level else round((xp - current[0]) / (next_level[0] - current[0]) * 100),
    }


async def log_event(db, user_id: int, name: str, metadata: dict | None = None):
    await db.execute(
        "INSERT INTO analytics_events (user_id, event_name, metadata) VALUES (?, ?, ?)",
        (user_id, name, json.dumps(metadata or {}, ensure_ascii=False)),
    )


@router.get("/game/profile")
async def game_profile(user_id: int | str = Query(...)):
    async with get_db() as db:
        user = await get_user_by_id_or_tg(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        today = date.today()
        last = date.fromisoformat(user["last_daily_claim"]) if user.get("last_daily_claim") else None
        can_claim = last != today
        next_claim = datetime.combine(today + timedelta(days=1), datetime.min.time()).replace(tzinfo=timezone.utc)
        return {
            "case_keys": user.get("case_keys", 0),
            "spins_count": user.get("spins_count", 0),
            "streak": user.get("login_streak", 0),
            "daily_available": can_claim,
            "next_daily_at": next_claim.isoformat(),
            "level": level_info(int(user.get("xp", 0))),
            "vip": bool(user.get("vip_until") and user["vip_until"] >= today.isoformat()),
        }


@router.post("/daily/claim")
async def claim_daily(req: ClaimTaskRequest):
    async with get_db() as db:
        user = await get_user_by_id_or_tg(db, req.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)) as cur:
            fresh = dict(await cur.fetchone())
        today = date.today()
        last = date.fromisoformat(fresh["last_daily_claim"]) if fresh.get("last_daily_claim") else None
        if last == today:
            raise HTTPException(status_code=409, detail="Ежедневная награда уже получена")
        streak = int(fresh.get("login_streak", 0)) + 1 if last == today - timedelta(days=1) else 1
        # Every seventh consecutive day also grants a case key.
        keys = 1 if streak % 7 == 0 else 0
        await db.execute(
            "UPDATE users SET login_streak=?, last_daily_claim=?, spins_count=spins_count+1, case_keys=case_keys+?, xp=xp+10 WHERE id=?",
            (streak, today.isoformat(), keys, fresh["id"]),
        )
        await log_event(db, fresh["id"], "daily_claim", {"streak": streak, "keys": keys})
        await db.commit()
    return {"success": True, "streak": streak, "spins": 1, "keys": keys, "message": f"День {streak}: +1 спин" + (" и +1 ключ" if keys else "")}


@router.get("/cases")
async def list_cases(user_id: int | str = Query(...)):
    async with get_db() as db:
        user = await get_user_by_id_or_tg(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
    public_cases = [{k: v for k, v in case.items() if k != "rewards"} for case in CASES.values()]
    return {"cases": public_cases, "case_keys": user.get("case_keys", 0), "stars_balance": user.get("stars_balance", 0), "balance": user["balance"]}


@router.post("/cases/{case_id}/open")
async def open_case(case_id: str, req: OpenCaseRequest):
    case = CASES.get(case_id)
    if not case or req.payment_method not in case["prices"]:
        raise HTTPException(status_code=404, detail="Кейс или способ оплаты не найден")
    async with get_db() as db:
        user = await get_user_by_id_or_tg(db, req.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute("SELECT * FROM users WHERE id=?", (user["id"],)) as cur:
            fresh = dict(await cur.fetchone())
        price = case["prices"][req.payment_method]
        field = {"key": "case_keys", "stars": "stars_balance", "rub": "balance"}[req.payment_method]
        if float(fresh.get(field, 0)) < price:
            raise HTTPException(status_code=400, detail="Недостаточно средств для открытия сундука")
        reward_type, reward_value, _ = random.choices(case["rewards"], weights=[r[2] for r in case["rewards"]], k=1)[0]
        reward_field = {"stars": "stars_balance", "rub": "balance", "spin": "spins_count", "key": "case_keys"}[reward_type]
        await db.execute(f"UPDATE users SET {field}={field}-? WHERE id=?", (price, fresh["id"]))
        await db.execute(f"UPDATE users SET {reward_field}={reward_field}+?, xp=xp+15 WHERE id=?", (reward_value, fresh["id"]))
        await db.execute("INSERT INTO case_openings (user_id, case_id, payment_method, reward_type, reward_value) VALUES (?, ?, ?, ?, ?)", (fresh["id"], case_id, req.payment_method, reward_type, reward_value))
        await log_event(db, fresh["id"], "case_open", {"case": case_id, "payment": req.payment_method, "reward": reward_type, "value": reward_value})
        await db.commit()
    labels = {"stars": "⭐", "rub": "₽", "spin": "спин", "key": "ключ"}
    return {"success": True, "reward": {"type": reward_type, "value": reward_value, "label": labels[reward_type]}, "message": f"Вы выиграли {reward_value} {labels[reward_type]}!"}


@router.get("/referrals/leaderboard")
async def referral_leaderboard(user_id: int | str = Query(...)):
    async with get_db() as db:
        user = await get_user_by_id_or_tg(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        async with db.execute("SELECT u.telegram_id, COALESCE(u.username, 'user') username, COUNT(r.id) referrals FROM users u LEFT JOIN users r ON r.referred_by=u.telegram_id GROUP BY u.id ORDER BY referrals DESC, u.id LIMIT 10") as cur:
            leaders = [dict(row) for row in await cur.fetchall()]
        async with db.execute("SELECT COUNT(*) count FROM users WHERE referred_by=?", (user["telegram_id"],)) as cur:
            own = (await cur.fetchone())["count"]
    return {"leaders": leaders, "my_referrals": own, "next_goal": next((n for n in (1, 3, 5, 10, 25, 50) if own < n), None)}


@router.get("/wheel/config")
async def get_wheel_config(user_id: int | str = Query(...)):
    async with get_db() as db:
        user = await get_user_by_id_or_tg(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
                
    return {
        "sectors": SECTORS,
        "spins_count": user.get("spins_count", 1),
        "balance": user["balance"],
        "stars_balance": user.get("stars_balance", 0.0),
        "spin_price_rub": 25.0
    }

@router.post("/wheel/spin")
async def spin_wheel(req: SpinWheelRequest):
    async with get_db() as db:
        user = await get_user_by_id_or_tg(db, req.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        if user["is_banned"]:
            raise HTTPException(status_code=403, detail="Пользователь заблокирован")
            
        actual_user_id = user["id"]
        has_spins = user.get("spins_count", 1) > 0
        if not has_spins:
            # Check if user has at least 25 RUB to buy a spin
            if user["balance"] < 25.0:
                raise HTTPException(status_code=400, detail="У вас нет бесплатных спинов. Пополните баланс (25 ₽ за спин) или выполните задания!")
            # Deduct 25 RUB
            await db.execute("UPDATE users SET balance = balance - 25.0 WHERE id = ?", (actual_user_id,))
            await db.execute("""
            INSERT INTO transactions (user_id, type, amount_rub, description)
            VALUES (?, 'purchase', -25.0, 'Покупка вращения Колеса Фортуны')
            """, (actual_user_id,))
        else:
            # Deduct 1 free spin
            await db.execute("UPDATE users SET spins_count = spins_count - 1 WHERE id = ?", (actual_user_id,))
            
        # Select winning sector weighted
        weights = [s["weight"] for s in SECTORS]
        winning_sector = random.choices(SECTORS, weights=weights, k=1)[0]
        
        # Apply reward
        r_type = winning_sector["type"]
        r_val = winning_sector["value"]
        
        if r_type == "stars":
            await db.execute("UPDATE users SET stars_balance = stars_balance + ? WHERE id = ?", (r_val, actual_user_id))
            await db.execute("""
            INSERT INTO transactions (user_id, type, amount_rub, stars_amount, description)
            VALUES (?, 'wheel_win', 0.0, ?, ?)
            """, (actual_user_id, r_val, f"Выигрыш в Колесе Фортуны: +{r_val} Stars"))
        elif r_type == "rub":
            await db.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (r_val, actual_user_id))
            await db.execute("""
            INSERT INTO transactions (user_id, type, amount_rub, description)
            VALUES (?, 'wheel_win', ?, ?)
            """, (actual_user_id, r_val, f"Выигрыш в Колесе Фортуны: +{r_val} ₽ на баланс"))
        elif r_type == "spin":
            await db.execute("UPDATE users SET spins_count = spins_count + ? WHERE id = ?", (r_val, actual_user_id))
        elif r_type == "discount":
            # Generate one-time promo code
            p_code = f"WHEEL-{uuid.uuid4().hex[:6].upper()}"
            await db.execute("""
            INSERT INTO promo_codes (code, type, value, max_uses, is_active)
            VALUES (?, 'discount', ?, 1, 1)
            """, (p_code, r_val))
            
        await db.execute("""
        INSERT INTO wheel_spins (user_id, reward_type, reward_value, reward_label)
        VALUES (?, ?, ?, ?)
        """, (actual_user_id, r_type, r_val, winning_sector["title"]))
        
        await db.commit()
        
        async with db.execute("SELECT spins_count, balance, stars_balance FROM users WHERE id = ?", (actual_user_id,)) as cur:
            updated_user = await cur.fetchone()
            
    return {
        "success": True,
        "sector_index": winning_sector["index"],
        "reward": winning_sector,
        "new_spins": updated_user["spins_count"],
        "new_balance": updated_user["balance"],
        "new_stars": updated_user["stars_balance"],
        "message": f"🎉 Поздравляем! Вы выиграли: {winning_sector['title']}"
    }

@router.get("/tasks")
async def get_tasks(user_id: int | str = Query(...)):
    async with get_db() as db:
        user = await get_user_by_id_or_tg(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
            
        actual_user_id = user["id"]
        actual_tg_id = user["telegram_id"]
                
        # Count user referrals
        async with db.execute("SELECT COUNT(*) as count FROM users WHERE referred_by = ?", (actual_tg_id,)) as cur:
            ref_count = (await cur.fetchone())["count"]
            
        # Count completed orders
        async with db.execute("SELECT COUNT(*) as count FROM orders WHERE user_id = ? AND status = 'completed'", (actual_user_id,)) as cur:
            orders_count = (await cur.fetchone())["count"]
            
        # Get completed tasks
        async with db.execute("SELECT task_id FROM user_tasks WHERE user_id = ? AND status = 'completed'", (actual_user_id,)) as cur:
            completed_task_ids = {row["task_id"] for row in await cur.fetchall()}
            
    # Probe every external provider before rendering buttons. A configured task is
    # not necessarily a task that still exists or can currently be verified.
    external_tasks = await discover_external_tasks()
    channel_url = await get_setting("task_channel_url", "")
    channel_reward = int(await get_setting("task_channel_reward", "50"))
    channel_available = external_tasks.get("subscribe_channel")
    
    tasks_list = [
        {
            "id": "invite_1",
            "title": "Пригласи друга",
            "desc": "Поделись реферальной ссылкой и пригласи 1 друга",
            "reward_stars": 10,
            "icon": "🤝",
            "progress": min(1, ref_count),
            "target": 1,
            "is_completed": "invite_1" in completed_task_ids,
            "action_type": "invite"
        }
    ]
    
    if channel_available and channel_available.available:
        tasks_list.append({
            "id": "subscribe_channel",
            "title": "Подпишись на наш канал",
            "desc": f"Подпишись на официальный канал StarVault ({channel_url.replace('https://t.me/', '@')})",
            "reward_stars": channel_reward,
            "icon": "📢",
            "progress": 1 if "subscribe_channel" in completed_task_ids else 0,
            "target": 1,
            "is_completed": "subscribe_channel" in completed_task_ids,
            "action_type": "link",
            "action_url": channel_url
        })
        
    tasks_list.extend([
        {
            "id": "invite_5",
            "title": "Пригласи 5 друзей",
            "desc": "Пригласи 5 активных рефералов в магазин",
            "reward_stars": 100,
            "icon": "👥",
            "progress": min(5, ref_count),
            "target": 5,
            "is_completed": "invite_5" in completed_task_ids,
            "action_type": "invite"
        },
        {
            "id": "first_order",
            "title": "Первая покупка",
            "desc": "Купите Звёзды или Telegram Premium",
            "reward_stars": 30,
            "icon": "🛒",
            "progress": min(1, orders_count),
            "target": 1,
            "is_completed": "first_order" in completed_task_ids,
            "action_type": "shop"
        }
    ])
    
    return {
        "tasks": tasks_list,
        "ref_count": ref_count,
        "stars_balance": user.get("stars_balance", 0.0)
    }

@router.post("/tasks/{task_id}/claim")
async def claim_task(task_id: str, req: ClaimTaskRequest):
    async with get_db() as db:
        user = await get_user_by_id_or_tg(db, req.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
            
        actual_user_id = user["id"]
        actual_tg_id = user["telegram_id"]
                
        # Serialize claims for this SQLite database. Validation and reward credit
        # then happen in one transaction, preventing two fast clicks from paying
        # the same task twice.
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT id FROM user_tasks WHERE user_id = ? AND task_id = ? AND status = 'completed'",
            (actual_user_id, task_id),
        ) as cur:
            if await cur.fetchone():
                raise HTTPException(status_code=400, detail="Награда за это задание уже получена!")

        # Validate requirements against fresh data (never trust the previously
        # rendered button).
        reward_stars = 0
        if task_id == "invite_1":
            async with db.execute("SELECT COUNT(*) as count FROM users WHERE referred_by = ?", (actual_tg_id,)) as cur:
                count = (await cur.fetchone())["count"]
            if count < 1:
                raise HTTPException(status_code=400, detail="Необходимо пригласить как минимум 1 друга!")
            reward_stars = 10

        elif task_id == "invite_5":
            async with db.execute("SELECT COUNT(*) as count FROM users WHERE referred_by = ?", (actual_tg_id,)) as cur:
                count = (await cur.fetchone())["count"]
            if count < 5:
                raise HTTPException(status_code=400, detail="Необходимо пригласить как минимум 5 друзей!")
            reward_stars = 100

        elif task_id == "first_order":
            async with db.execute("SELECT COUNT(*) as count FROM orders WHERE user_id = ? AND status = 'completed'", (actual_user_id,)) as cur:
                count = (await cur.fetchone())["count"]
            if count < 1:
                raise HTTPException(status_code=400, detail="Сначала совершите хотя бы одну успешную покупку!")
            reward_stars = 30

        elif task_id == "subscribe_channel":
            availability = (await discover_external_tasks()).get("subscribe_channel")
            if not availability or not availability.available:
                raise HTTPException(
                    status_code=409,
                    detail="Задание больше недоступно. Обновите список заданий.",
                )
            reward_stars = int(await get_setting("task_channel_reward", "50"))
            try:
                subscribed = await is_channel_member(actual_tg_id)
            except Exception as exc:
                logger.warning("Telegram membership verification failed: %s", exc)
                raise HTTPException(
                    status_code=503,
                    detail="Не удалось проверить подписку. Попробуйте ещё раз позже.",
                ) from exc
            if not subscribed:
                raise HTTPException(status_code=400, detail="Вы ещё не подписаны на канал!")
        else:
            raise HTTPException(status_code=404, detail="Неизвестное задание")

        # Reserve the unique task row before crediting. Existing legacy
        # ``available`` rows are promoted; a completed row was rejected above.
        await db.execute("""
        INSERT INTO user_tasks (user_id, task_id, status, completed_at)
        VALUES (?, ?, 'completed', CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, task_id) DO UPDATE SET
            status = 'completed', completed_at = CURRENT_TIMESTAMP
        WHERE user_tasks.status != 'completed'
        """, (actual_user_id, task_id))

        # Credit only after the claim has been reserved in this transaction.
        await db.execute("UPDATE users SET stars_balance = stars_balance + ? WHERE id = ?", (reward_stars, actual_user_id))

        # Add transaction record
        await db.execute("""
        INSERT INTO transactions (user_id, type, amount_rub, stars_amount, description)
        VALUES (?, 'task_reward', 0.0, ?, ?)
        """, (actual_user_id, reward_stars, f"Награда за задание '{task_id}': +{reward_stars} Stars"))
        
        await db.commit()
        
    return {
        "success": True,
        "task_id": task_id,
        "reward_stars": reward_stars,
        "message": f"🎉 Задание выполнено! Вам начислено +{reward_stars} Stars ⭐"
    }
