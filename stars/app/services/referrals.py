import aiosqlite
import logging
from app.database import get_db

logger = logging.getLogger(__name__)

class PromoService:
    @staticmethod
    async def validate_promo(code: str, user_id: int) -> dict:
        clean_code = code.strip().upper()
        async with get_db() as db:
            async with db.execute("SELECT * FROM promo_codes WHERE code = ? AND is_active = 1", (clean_code,)) as cur:
                promo = await cur.fetchone()
                if not promo:
                    return {"valid": False, "message": "Промокод не найден или деактивирован"}
                    
            if promo["current_uses"] >= promo["max_uses"]:
                return {"valid": False, "message": "Лимит активаций этого промокода исчерпан"}
                
            # Check if user already used this promo
            async with db.execute("SELECT id FROM promo_redemptions WHERE promo_id = ? AND user_id = ?", (promo["id"], user_id)) as cur:
                redemption = await cur.fetchone()
                if redemption:
                    return {"valid": False, "message": "Вы уже активировали данный промокод"}
                    
            return {
                "valid": True,
                "promo_id": promo["id"],
                "code": promo["code"],
                "type": promo["type"], # 'balance' or 'discount'
                "value": promo["value"],
                "message": f"Промокод активен: {'+' + str(promo['value']) + ' ₽ на баланс' if promo['type'] == 'balance' else 'скидка ' + str(promo['value']) + '%'}"
            }

    @staticmethod
    async def apply_promo(code: str, user_id: int) -> dict:
        validation = await PromoService.validate_promo(code, user_id)
        if not validation["valid"]:
            return validation
            
        promo_type = validation["type"]
        promo_value = validation["value"]
        promo_id = validation["promo_id"]
        
        async with get_db() as db:
            # Record redemption
            await db.execute("INSERT INTO promo_redemptions (promo_id, user_id) VALUES (?, ?)", (promo_id, user_id))
            await db.execute("UPDATE promo_codes SET current_uses = current_uses + 1 WHERE id = ?", (promo_id,))
            
            if promo_type == "balance":
                await db.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (promo_value, user_id))
                await db.execute("""
                INSERT INTO transactions (user_id, type, amount_rub, description)
                VALUES (?, 'admin_adjust', ?, ?)
                """, (user_id, promo_value, f"Активация промокода {code}"))
                await db.commit()
                return {"success": True, "type": "balance", "value": promo_value, "message": f"Баланс успешно пополнен на {promo_value} ₽!"}
            else:
                await db.commit()
                return {"success": True, "type": "discount", "value": promo_value, "message": f"Скидка {promo_value}% применена к текущему заказу!"}
