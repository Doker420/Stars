import json
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.database import get_db
from app.web.routes_api import get_user_by_id_or_tg

router = APIRouter(prefix="/api/campaigns", tags=["Promotion campaigns"])

FORMATS = {
    "channel_subscribe": {"icon": "📢", "title": "Подписка на канал", "rate": 3.0, "verification": "telegram", "description": "Проверка подписки через Telegram"},
    "bot_start": {"icon": "🤖", "title": "Запуск бота", "rate": 2.0, "verification": "callback", "description": "Запуск бота по deep-link"},
    "post_view": {"icon": "📝", "title": "Просмотр публикации", "rate": 1.0, "verification": "delayed", "description": "Переход и выдержка времени"},
    "post_reaction": {"icon": "👍", "title": "Реакция на пост", "rate": 2.5, "verification": "telegram", "description": "Обычная реакция на публикацию"},
    "poll_vote": {"icon": "📊", "title": "Голосование в опросе", "rate": 3.0, "verification": "mixed", "description": "Автопроверка опроса нашего бота или подтверждение внешнего"},
    "post_comment": {"icon": "💬", "title": "Комментарий к посту", "rate": 4.0, "verification": "moderation", "description": "Комментарий с проверкой"},
    "story_view": {"icon": "👁", "title": "Просмотр истории", "rate": 2.0, "verification": "delayed", "description": "Просмотр Telegram Story"},
    "premium_reaction": {"icon": "💎", "title": "Premium-реакция", "rate": 5.0, "verification": "telegram", "premium_required": True, "description": "Кастомная реакция от Telegram Premium"},
    "channel_boost": {"icon": "🚀", "title": "Буст канала", "rate": 15.0, "verification": "telegram", "premium_required": True, "description": "Буст канала пользователем Premium"},
    "custom_link": {"icon": "🔗", "title": "Своё задание", "rate": 4.0, "verification": "moderation", "description": "Любое действие по безопасной ссылке"},
}


class CreateCampaignRequest(BaseModel):
    user_id: int | str
    task_type: str
    title: str = Field(min_length=3, max_length=80)
    target_url: str = Field(min_length=8, max_length=500)
    target_count: int = Field(ge=10, le=100000)
    premium_only: bool = False
    poll_mode: str | None = None


def valid_target_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


@router.get("/formats")
async def campaign_formats():
    return {"formats": [{"id": key, **value} for key, value in FORMATS.items()], "active_limit": 3, "premium_targeting_markup_percent": 25}


@router.get("/mine")
async def my_campaigns(user_id: int | str = Query(...)):
    async with get_db() as db:
        user = await get_user_by_id_or_tg(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        async with db.execute("SELECT * FROM campaigns WHERE owner_user_id=? ORDER BY id DESC LIMIT 50", (user["id"],)) as cur:
            campaigns = [dict(row) for row in await cur.fetchall()]
    for campaign in campaigns:
        campaign["metadata"] = json.loads(campaign.get("metadata") or "{}")
    return {"campaigns": campaigns, "active": sum(c["status"] == "active" for c in campaigns), "limit": 3}


@router.post("")
async def create_campaign(req: CreateCampaignRequest):
    task_format = FORMATS.get(req.task_type)
    if not task_format:
        raise HTTPException(status_code=400, detail="Неизвестный формат задания")
    if not valid_target_url(req.target_url):
        raise HTTPException(status_code=400, detail="Нужна безопасная ссылка https://")
    if req.task_type == "poll_vote" and req.poll_mode not in {"bot", "external"}:
        raise HTTPException(status_code=400, detail="Для опроса выберите режим bot или external")

    premium_only = req.premium_only or bool(task_format.get("premium_required"))
    rate = float(task_format["rate"]) * (1.25 if req.premium_only and not task_format.get("premium_required") else 1.0)
    budget = round(rate * req.target_count, 2)
    verification = "poll_answer" if req.task_type == "poll_vote" and req.poll_mode == "bot" else task_format["verification"]
    if req.task_type == "poll_vote" and req.poll_mode == "external":
        verification = "moderation"

    async with get_db() as db:
        user = await get_user_by_id_or_tg(db, req.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute("SELECT * FROM users WHERE id=?", (user["id"],)) as cur:
            fresh = dict(await cur.fetchone())
        async with db.execute("SELECT COUNT(*) count FROM campaigns WHERE owner_user_id=? AND status='active'", (fresh["id"],)) as cur:
            active = (await cur.fetchone())["count"]
        if active >= 3:
            raise HTTPException(status_code=409, detail="Достигнут лимит: 3 активные кампании")
        if float(fresh.get("stars_balance", 0)) < budget:
            raise HTTPException(status_code=400, detail=f"Недостаточно Stars. Бюджет кампании: {budget:g} ⭐")
        campaign_uid = f"CMP-{uuid.uuid4().hex[:10].upper()}"
        metadata = json.dumps({"poll_mode": req.poll_mode} if req.poll_mode else {}, ensure_ascii=False)
        await db.execute("UPDATE users SET stars_balance=stars_balance-? WHERE id=?", (budget, fresh["id"]))
        await db.execute("""INSERT INTO campaigns (owner_user_id,campaign_uid,task_type,title,target_url,target_count,rate_stars,budget_stars,premium_only,verification_mode,metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (fresh["id"], campaign_uid, req.task_type, req.title.strip(), req.target_url.strip(), req.target_count, rate, budget, int(premium_only), verification, metadata))
        await db.execute("INSERT INTO analytics_events (user_id,event_name,metadata) VALUES (?,?,?)", (fresh["id"], "campaign_created", json.dumps({"campaign_uid": campaign_uid, "type": req.task_type, "budget": budget})))
        await db.commit()
    return {"success": True, "campaign_uid": campaign_uid, "rate_stars": rate, "budget_stars": budget, "premium_only": premium_only, "verification_mode": verification}


@router.get("/available")
async def available_campaigns(user_id: int | str = Query(...), limit: int = Query(20, ge=1, le=50)):
    async with get_db() as db:
        user = await get_user_by_id_or_tg(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        premium = bool(user.get("telegram_is_premium"))
        async with db.execute("""SELECT c.* FROM campaigns c WHERE c.status='active' AND c.completed_count<c.target_count AND c.owner_user_id!=? AND (c.premium_only=0 OR ?=1) AND NOT EXISTS (SELECT 1 FROM campaign_completions cc WHERE cc.campaign_id=c.id AND cc.user_id=?) ORDER BY c.premium_only DESC,c.rate_stars DESC,c.id LIMIT ?""", (user["id"], int(premium), user["id"], limit)) as cur:
            campaigns = [dict(row) for row in await cur.fetchall()]
    return {"campaigns": campaigns, "telegram_premium": premium}
