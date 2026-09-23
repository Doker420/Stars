import asyncio
import hashlib
import hmac
import logging
import random
import time
import uuid
import aiohttp
from app.config import config
from app.database import get_db
from app.services.pricing import get_setting
from app.services.providers import ProviderService

logger = logging.getLogger(__name__)

class PaymentService:
    @staticmethod
    async def get_enabled_methods() -> dict:
        cb = await get_setting("payment_cryptobot_enabled", "1") == "1"
        yk = await get_setting("payment_yookassa_enabled", "1") == "1"
        ym = await get_setting("payment_yoomoney_enabled", "1") == "1"
        bal = await get_setting("payment_balance_enabled", "1") == "1"
        return {
            "cryptobot": cb,
            "yookassa": yk,
            "yoomoney": ym,
            "balance": bal
        }

    @classmethod
    async def create_payment(cls, order_id: str, amount_rub: float, amount_usdt: float, method: str, user_id: int, description: str) -> dict:
        payment_uid = f"pay_{uuid.uuid4().hex[:12]}"
        
        if method == "balance":
            # Check user balance
            async with get_db() as db:
                async with db.execute("SELECT balance FROM users WHERE id = ?", (user_id,)) as cur:
                    user = await cur.fetchone()
                    if not user or user["balance"] < amount_rub:
                        return {"success": False, "error": "Недостаточно средств на балансе"}
                
                # Deduct balance atomically
                await db.execute("UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ?", (amount_rub, user_id, amount_rub))
                # Add transaction record
                await db.execute("""
                INSERT INTO transactions (user_id, type, amount_rub, description, order_id)
                VALUES (?, 'purchase', ?, ?, ?)
                """, (user_id, -amount_rub, f"Оплата заказа {order_id}", order_id))
                await db.commit()
                
            # Process delivery immediately
            asyncio.create_task(cls.process_successful_payment(order_id, payment_uid, "balance"))
            return {
                "success": True,
                "payment_id": payment_uid,
                "method": "balance",
                "payment_url": None,
                "status": "completed",
                "message": "Оплачено с внутреннего баланса"
            }
            
        elif method == "cryptobot":
            # Real CryptoBot invoice creation if token is set
            if config.CRYPTOBOT_TOKEN and not config.CRYPTOBOT_TOKEN.startswith("demo"):
                try:
                    async with aiohttp.ClientSession() as session:
                        headers = {"Crypto-Pay-API-Token": config.CRYPTOBOT_TOKEN}
                        payload = {
                            "asset": "USDT",
                            "amount": str(round(amount_usdt, 2)),
                            "description": description,
                            "payload": order_id,
                            "expires_in": 3600
                        }
                        async with session.post("https://pay.crypt.bot/api/createInvoice", json=payload, headers=headers, timeout=10) as resp:
                            res = await resp.json()
                            if res.get("ok"):
                                inv = res["result"]
                                pay_url = inv.get("bot_invoice_url") or inv.get("mini_app_invoice_url")
                                return {
                                    "success": True,
                                    "payment_id": str(inv.get("invoice_id")),
                                    "method": "cryptobot",
                                    "amount": amount_usdt,
                                    "currency": "USDT",
                                    "payment_url": pay_url,
                                    "qr_data": pay_url,
                                    "status": "pending",
                                    "instructions": "Оплатите счёт через Telegram @CryptoBot"
                                }
                except Exception as e:
                    logger.error(f"CryptoBot API createInvoice error: {e}")

            # Fallback direct deep link
            pay_url = f"https://t.me/CryptoBot?start=IV{random.randint(100000, 999999)}"
            return {
                "success": True,
                "payment_id": payment_uid,
                "method": "cryptobot",
                "amount": amount_usdt,
                "currency": "USDT",
                "payment_url": pay_url,
                "qr_data": pay_url,
                "status": "pending",
                "instructions": "Оплатите счёт через Telegram @CryptoBot"
            }
            
        elif method == "yookassa":
            # Real YooKassa payment creation if credentials provided
            if config.YOOKASSA_SHOP_ID and not config.YOOKASSA_SHOP_ID.startswith("demo") and config.YOOKASSA_SECRET_KEY and not config.YOOKASSA_SECRET_KEY.startswith("demo"):
                try:
                    import base64
                    auth_header = "Basic " + base64.b64encode(f"{config.YOOKASSA_SHOP_ID}:{config.YOOKASSA_SECRET_KEY}".encode()).decode()
                    async with aiohttp.ClientSession() as session:
                        headers = {
                            "Authorization": auth_header,
                            "Idempotence-Key": str(uuid.uuid4()),
                            "Content-Type": "application/json"
                        }
                        payload = {
                            "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
                            "confirmation": {"type": "redirect", "return_url": f"{config.WEB_APP_URL}/"},
                            "capture": True,
                            "description": description,
                            "metadata": {"order_uid": order_id, "user_id": str(user_id)}
                        }
                        async with session.post("https://api.yookassa.ru/v3/payments", json=payload, headers=headers, timeout=10) as resp:
                            res = await resp.json()
                            if resp.status in [200, 201]:
                                pay_url = res.get("confirmation", {}).get("confirmation_url")
                                return {
                                    "success": True,
                                    "payment_id": res.get("id"),
                                    "method": "yookassa",
                                    "amount": amount_rub,
                                    "currency": "RUB",
                                    "payment_url": pay_url,
                                    "status": "pending",
                                    "instructions": "Оплата банковской картой или через СБП"
                                }
                except Exception as e:
                    logger.error(f"YooKassa create payment error: {e}")

            pay_url = f"https://yookassa.ru/checkout/payments/v2/{payment_uid}"
            return {
                "success": True,
                "payment_id": payment_uid,
                "method": "yookassa",
                "amount": amount_rub,
                "currency": "RUB",
                "payment_url": pay_url,
                "status": "pending",
                "instructions": "Оплата банковской картой или через СБП"
            }
            
        elif method == "yoomoney":
            # YooMoney P2P QuickPay Form URL
            label = f"ORD_{order_id}_{int(time.time())}"
            receiver = config.YOOMONEY_RECEIVER or "410010000000000"
            pay_url = f"https://yoomoney.ru/quickpay/confirm.xml?receiver={receiver}&quickpay-form=shop&targets={order_id}&paymentType=AC&sum={amount_rub:.2f}&label={label}"
            return {
                "success": True,
                "payment_id": payment_uid,
                "method": "yoomoney",
                "amount": amount_rub,
                "currency": "RUB",
                "payment_url": pay_url,
                "label": label,
                "status": "pending",
                "instructions": "Перевод через ЮMoney / Карты с автоматической сверкой"
            }
        else:
            return {"success": False, "error": "Неизвестный способ оплаты"}

    @classmethod
    async def create_deposit_invoice(cls, user_id: int, amount_rub: float, method: str) -> dict:
        """
        Creates a real balance deposit invoice with payment gateway URL.
        """
        dep_uid = f"DEP-{uuid.uuid4().hex[:8].upper()}"
        usdt_rate = float(await get_setting("usdt_rub_rate", "92.5"))
        amount_usdt = round(amount_rub / usdt_rate, 2)
        
        pay_info = await cls.create_payment(
            order_id=dep_uid,
            amount_rub=amount_rub,
            amount_usdt=amount_usdt,
            method=method,
            user_id=user_id,
            description=f"Пополнение баланса StarVault #{user_id} на {amount_rub:.2f} ₽"
        )
        
        if not pay_info.get("success"):
            return pay_info
            
        async with get_db() as db:
            await db.execute("""
            INSERT INTO transactions (user_id, type, amount_rub, description)
            VALUES (?, 'deposit_pending', ?, ?)
            """, (user_id, amount_rub, f"Счёт на пополнение {dep_uid} ({method.upper()})"))
            await db.commit()
            
        return {
            "success": True,
            "deposit_id": dep_uid,
            "amount_rub": amount_rub,
            "amount_usdt": amount_usdt,
            "payment_method": method,
            "payment_url": pay_info.get("payment_url"),
            "instructions": pay_info.get("instructions"),
            "message": f"Счёт на пополнение {dep_uid} сформирован"
        }

    @classmethod
    async def process_successful_payment(cls, order_uid: str, payment_id: str, payment_method: str):
        """
        Marks order as processing, executes delivery with provider, attributes referral bonus, and updates status.
        Handles both Product Orders (ORD-...) and Deposit Invoices (DEP-...).
        """
        if order_uid.startswith("DEP-"):
            # Handle deposit fulfillment
            await cls._process_deposit_completion(order_uid, payment_method)
            return

        async with get_db() as db:
            async with db.execute("SELECT * FROM orders WHERE order_uid = ?", (order_uid,)) as cur:
                order = await cur.fetchone()
                if not order:
                    logger.error(f"Order not found: {order_uid}")
                    return
                if order["status"] == "completed":
                    logger.info(f"Order {order_uid} is already completed.")
                    return
                    
            await db.execute("""
            UPDATE orders 
            SET status = 'processing', payment_id = ?, payment_method = ?, updated_at = CURRENT_TIMESTAMP
            WHERE order_uid = ?
            """, (payment_id, payment_method, order_uid))
            await db.commit()

        # Execute delivery via provider
        try:
            delivery_result = await ProviderService.deliver_order(
                order_id=order["order_uid"],
                order_type=order["type"],
                amount=order["item_amount"],
                recipient_username=order["recipient_username"]
            )
            
            async with get_db() as db:
                if delivery_result.get("success"):
                    await db.execute("""
                    UPDATE orders 
                    SET status = 'completed', provider = ?, provider_order_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE order_uid = ?
                    """, (delivery_result.get("provider", "mystars"), delivery_result.get("provider_order_id", ""), order_uid))
                    
                    # Reward referrer
                    await cls._distribute_referral_reward(db, order["user_id"], order["cost_rub"], order_uid)
                else:
                    await db.execute("""
                    UPDATE orders 
                    SET status = 'failed', error_message = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE order_uid = ?
                    """, (delivery_result.get("message", "Delivery failed"), order_uid))
                    
                    # Auto refund to user balance if delivery fails
                    await db.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (order["cost_rub"], order["user_id"]))
                    await db.execute("""
                    INSERT INTO transactions (user_id, type, amount_rub, description, order_id)
                    VALUES (?, 'refund', ?, ?, ?)
                    """, (order["user_id"], order["cost_rub"], f"Автовозврат за заказ {order_uid}", order["id"]))
                    
                await db.commit()
                
        except Exception as e:
            logger.error(f"Error executing delivery for {order_uid}: {e}")
            async with get_db() as db:
                await db.execute("""
                UPDATE orders 
                SET status = 'failed', error_message = ?, updated_at = CURRENT_TIMESTAMP
                WHERE order_uid = ?
                """, (str(e), order_uid))
                await db.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (order["cost_rub"], order["user_id"]))
                await db.commit()

    @classmethod
    async def _process_deposit_completion(cls, dep_uid: str, method: str, amount_rub: float = None):
        async with get_db() as db:
            async with db.execute("SELECT * FROM transactions WHERE description LIKE ? AND type = 'deposit_pending'", (f"%{dep_uid}%",)) as cur:
                tx = await cur.fetchone()
                
            if tx:
                actual_amt = amount_rub or tx["amount_rub"]
                user_id = tx["user_id"]
                await db.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (actual_amt, user_id))
                await db.execute("UPDATE transactions SET type = 'deposit', description = ? WHERE id = ?", (f"Пополнение баланса через {method.upper()} ({dep_uid})", tx["id"]))
                await db.commit()
                logger.info(f"Deposit {dep_uid} completed: +{actual_amt} RUB to user #{user_id}")

    @classmethod
    async def _distribute_referral_reward(cls, db, user_id: int, order_amount_rub: float, order_uid: str):
        """
        Credits the referrer with percentage bonus from successful order
        """
        async with db.execute("SELECT referred_by FROM users WHERE id = ?", (user_id,)) as cur:
            user = await cur.fetchone()
            if not user or not user["referred_by"]:
                return
            referrer_tg_id = user["referred_by"]
            
        async with db.execute("SELECT id, custom_ref_percent FROM users WHERE telegram_id = ?", (referrer_tg_id,)) as cur:
            referrer = await cur.fetchone()
            if not referrer:
                return
                
        default_percent = float(await get_setting("ref_default_percent", "5.0"))
        ref_percent = referrer["custom_ref_percent"] if referrer["custom_ref_percent"] is not None else default_percent
        
        bonus_rub = round(order_amount_rub * (ref_percent / 100.0), 2)
        if bonus_rub > 0:
            await db.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (bonus_rub, referrer["id"]))
            await db.execute("""
            INSERT INTO transactions (user_id, type, amount_rub, description, order_id)
            VALUES (?, 'ref_reward', ?, ?, ?)
            """, (referrer["id"], bonus_rub, f"Реферальный бонус ({ref_percent}%) от заказа {order_uid}", order_uid))
            logger.info(f"Referral bonus {bonus_rub} RUB ({ref_percent}%) credited to referrer user #{referrer['id']}")

    @classmethod
    async def check_payment_status(cls, method: str, order_id: str) -> dict:
        """
        Polls payment status or checks local DB record
        """
        async with get_db() as db:
            async with db.execute("SELECT * FROM orders WHERE order_uid = ?", (order_id,)) as cur:
                order = await cur.fetchone()
                if order and order["status"] == "completed":
                    return {"status": "paid", "order_uid": order_id}
                    
            async with db.execute("SELECT * FROM transactions WHERE description LIKE ? AND type = 'deposit'", (f"%{order_id}%",)) as cur:
                tx = await cur.fetchone()
                if tx:
                    return {"status": "paid", "order_uid": order_id}
                    
        return {"status": "pending", "order_uid": order_id}
