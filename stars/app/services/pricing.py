import aiosqlite
from app.database import get_db

async def get_setting(key: str, default: str = "") -> str:
    async with get_db() as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row["value"] if row else default

async def set_setting(key: str, value: str):
    async with get_db() as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        await db.commit()

async def calculate_stars_price(stars_count: int, discount_percent: float = 0.0) -> dict:
    base_rate_rub = float(await get_setting("stars_base_rate_rub", "1.45"))
    markup_percent = float(await get_setting("stars_markup_percent", "14.0"))
    usdt_rub_rate = float(await get_setting("usdt_rub_rate", "92.5"))
    ton_usdt_rate = float(await get_setting("ton_usdt_rate", "3.20"))
    
    # Base cost from provider
    base_cost_rub = round(stars_count * base_rate_rub, 2)
    # Price with shop markup
    shop_rate_rub = base_rate_rub * (1.0 + markup_percent / 100.0)
    total_rub_before_discount = stars_count * shop_rate_rub
    
    if discount_percent > 0:
        final_rub = round(total_rub_before_discount * (1.0 - discount_percent / 100.0), 2)
    else:
        final_rub = round(total_rub_before_discount, 2)
        
    margin_rub = round(final_rub - base_cost_rub, 2)
    final_usdt = round(final_rub / usdt_rub_rate, 2)
    final_ton = round(final_usdt / ton_usdt_rate, 2)
    
    return {
        "stars_count": stars_count,
        "base_rate_rub": base_rate_rub,
        "rate_rub_per_star": round(shop_rate_rub, 2),
        "base_cost_rub": base_cost_rub,
        "total_rub": final_rub,
        "total_usdt": final_usdt,
        "total_ton": final_ton,
        "margin_rub": margin_rub,
        "discount_applied": discount_percent
    }

async def calculate_premium_price(months: int, discount_percent: float = 0.0) -> dict:
    base_key = f"premium_{months}m_base_rub"
    default_base = "890.0" if months == 3 else ("1420.0" if months == 6 else "2490.0")
    base_cost_rub = float(await get_setting(base_key, default_base))
    markup_percent = float(await get_setting("premium_markup_percent", "12.0"))
    usdt_rub_rate = float(await get_setting("usdt_rub_rate", "92.5"))
    ton_usdt_rate = float(await get_setting("ton_usdt_rate", "3.20"))
    
    shop_cost_rub = base_cost_rub * (1.0 + markup_percent / 100.0)
    if discount_percent > 0:
        final_rub = round(shop_cost_rub * (1.0 - discount_percent / 100.0), 2)
    else:
        final_rub = round(shop_cost_rub, 2)
        
    margin_rub = round(final_rub - base_cost_rub, 2)
    final_usdt = round(final_rub / usdt_rub_rate, 2)
    final_ton = round(final_usdt / ton_usdt_rate, 2)
    
    return {
        "months": months,
        "base_cost_rub": base_cost_rub,
        "total_rub": final_rub,
        "total_usdt": final_usdt,
        "total_ton": final_ton,
        "margin_rub": margin_rub,
        "discount_applied": discount_percent
    }
