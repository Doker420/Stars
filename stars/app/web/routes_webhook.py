import hashlib
import hmac
import logging
import time
from urllib.parse import parse_qs
from fastapi import APIRouter, Request, HTTPException, Response
from app.config import config
from app.database import get_db
from app.services.payments import PaymentService
from app.services.mystars_client import MyStarsClient
from app.services.security import log_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["Payment & FaaS Webhooks"])

@router.post("/mystars")
@router.post("/api/webhooks/mystars")
async def mystars_webhook(request: Request):
    """
    Official MyStars FaaS Webhook processor (https://mystars.tg/docs)
    Verifies X-FaaS-Signature header with HMAC-SHA256
    Handles order.delivered, order.failed, order.reversed, order.expired
    """
    raw_body = await request.body()
    sig_header = request.headers.get("x-faas-signature") or request.headers.get("X-FaaS-Signature")
    
    # If live secret is configured and not default mock, verify signature
    if config.MYSTARS_WEBHOOK_SECRET and not config.MYSTARS_WEBHOOK_SECRET.startswith("demo"):
        if not MyStarsClient.verify_webhook_signature(raw_body, sig_header or ""):
            logger.warning("Invalid MyStars X-FaaS-Signature webhook signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

    audit_entry = None
    try:
        data = await request.json()
        logger.info(f"Received MyStars webhook payload: {data}")
        
        event_type = data.get("event") or f"order.{data.get('status')}"
        mystars_order_id = data.get("order_id")
        status = data.get("status")
        purchase_tx = data.get("purchase_tx")
        reversal_tx = data.get("reversal_tx")
        failure_reason = data.get("failure_reason")

        async with get_db() as db:
            async with db.execute(
                "SELECT * FROM orders WHERE provider_order_id = ? OR order_uid = ? OR provider_order_id LIKE ?", 
                (mystars_order_id, mystars_order_id, f"%{mystars_order_id}%")
            ) as cur:
                order = await cur.fetchone()
                
            if order:
                order_uid = order["order_uid"]
                if status == "delivered" or event_type == "order.delivered":
                    await db.execute("""
                    UPDATE orders 
                    SET status = 'completed', provider_order_id = COALESCE(?, provider_order_id), updated_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                    """, (purchase_tx, order["id"]))
                    audit_entry = (1, "MYSTARS_DELIVERED", f"Заказ {order_uid} доставлен. TX: {purchase_tx}")
                    logger.info(f"MyStars webhook: order {order_uid} successfully marked as delivered ({purchase_tx}).")
                    
                elif status == "failed" or event_type == "order.failed":
                    await db.execute("""
                    UPDATE orders 
                    SET status = 'failed', error_message = ?, updated_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                    """, (failure_reason or "Ошибка доставки в MyStars", order["id"]))
                    audit_entry = (1, "MYSTARS_FAILED", f"Заказ {order_uid} не выполнен: {failure_reason}")
                    
                elif status == "reversed" or event_type == "order.reversed":
                    # Refund cost to user balance
                    await db.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (order["cost_rub"], order["user_id"]))
                    await db.execute("""
                    INSERT INTO transactions (user_id, type, amount_rub, description, order_id)
                    VALUES (?, 'refund', ?, ?, ?)
                    """, (order["user_id"], order["cost_rub"], f"Автоматический возврат средств за {order_uid} (Reversal: {reversal_tx})", order["id"]))
                    await db.execute("""
                    UPDATE orders 
                    SET status = 'reversed', error_message = ?, updated_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                    """, (f"Reversed in TON: {reversal_tx}", order["id"]))
                    audit_entry = (1, "MYSTARS_REVERSED", f"Возврат {order['cost_rub']} ₽ пользователю #{order['user_id']} за {order_uid}")
                    
                elif status == "expired" or event_type == "order.expired":
                    await db.execute("""
                    UPDATE orders 
                    SET status = 'expired', updated_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                    """, (order["id"],))
                    
            await db.commit()
            
        if audit_entry:
            await log_audit(audit_entry[0], audit_entry[1], audit_entry[2])
            
    except Exception as e:
        logger.error(f"Error processing MyStars webhook: {e}")
        
    return {"ok": True, "received": True}

@router.post("/cryptobot")
async def cryptobot_webhook(request: Request):
    """
    CryptoPay webhook processor with HMAC signature verification
    """
    body = await request.body()
    signature = request.headers.get("crypto-pay-api-signature", "")
    
    if config.CRYPTOBOT_TOKEN and not config.CRYPTOBOT_TOKEN.startswith("demo"):
        secret = hashlib.sha256(config.CRYPTOBOT_TOKEN.encode()).digest()
        check_signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, check_signature):
            logger.warning("Invalid CryptoBot webhook signature")
            raise HTTPException(status_code=400, detail="Invalid signature")
            
    try:
        data = await request.json()
        payload = data.get("payload", {})
        if data.get("update_type") == "invoice_paid":
            order_uid = payload.get("payload") or payload.get("custom_id")
            invoice_id = str(payload.get("invoice_id"))
            if order_uid:
                await PaymentService.process_successful_payment(order_uid, invoice_id, "cryptobot")
                logger.info(f"CryptoBot webhook: order {order_uid} marked as paid.")
    except Exception as e:
        logger.error(f"Error handling cryptobot webhook: {e}")
        
    return {"ok": True}

@router.post("/yookassa")
async def yookassa_webhook(request: Request):
    """
    YooKassa webhook processor
    """
    try:
        data = await request.json()
        event = data.get("event")
        payment_obj = data.get("object", {})
        
        if event == "payment.succeeded":
            payment_id = payment_obj.get("id")
            metadata = payment_obj.get("metadata", {})
            order_uid = metadata.get("order_uid")
            if order_uid:
                await PaymentService.process_successful_payment(order_uid, payment_id, "yookassa")
                logger.info(f"YooKassa webhook: order {order_uid} marked as paid.")
    except Exception as e:
        logger.error(f"Error handling YooKassa webhook: {e}")
        
    return Response(status_code=200)

@router.post("/yoomoney")
async def yoomoney_webhook(request: Request):
    """
    YooMoney HTTP notification processor (with SHA1 check and robust multi-format body parser)
    """
    try:
        raw_body = await request.body()
        content_type = request.headers.get("content-type", "")
        
        if "application/json" in content_type:
            data = await request.json()
        else:
            parsed = parse_qs(raw_body.decode("utf-8", errors="ignore"))
            data = {k: v[0] if isinstance(v, list) and len(v) > 0 else v for k, v in parsed.items()}
            
        notification_type = data.get("notification_type", "")
        operation_id = data.get("operation_id", "")
        amount = data.get("amount", "")
        currency = data.get("currency", "")
        datetime_str = data.get("datetime", "")
        sender = data.get("sender", "")
        codepro = data.get("codepro", "")
        label = data.get("label", "")
        sha1_hash = data.get("sha1_hash", "")
        
        # Verify SHA-1 if secret configured
        if config.YOOMONEY_SECRET and not config.YOOMONEY_SECRET.startswith("demo"):
            check_str = f"{notification_type}&{operation_id}&{amount}&{currency}&{datetime_str}&{sender}&{codepro}&{config.YOOMONEY_SECRET}&{label}"
            calculated_sha1 = hashlib.sha1(check_str.encode()).hexdigest()
            if calculated_sha1.lower() != sha1_hash.lower():
                logger.warning(f"Invalid YooMoney SHA1 hash: {sha1_hash} vs {calculated_sha1}")
                return Response(status_code=400)
                
        # Extract order UID from label (e.g. ORD_ORD-ABCD1234_1700000000 or ORD_DEP-ABCD1234_1700000000)
        if label.startswith("ORD_"):
            parts = label.split("_")
            if len(parts) >= 2:
                order_uid = parts[1]
                await PaymentService.process_successful_payment(order_uid, operation_id, "yoomoney")
                logger.info(f"YooMoney webhook: order/deposit {order_uid} marked as paid.")
    except Exception as e:
        logger.error(f"Error handling YooMoney webhook: {e}")
        
    return Response(status_code=200)

@router.post("/sync-pending")
async def sync_pending_orders():
    """
    Background worker endpoint: rechecks and reconciles pending orders
    """
    async with get_db() as db:
        async with db.execute("SELECT * FROM orders WHERE status = 'pending' AND created_at < datetime('now', '-30 minutes')") as cur:
            old_orders = await cur.fetchall()
            for o in old_orders:
                logger.info(f"Cleaning expired pending order: {o['order_uid']}")
                await db.execute("UPDATE orders SET status = 'failed', error_message = 'Время на оплату истекло' WHERE id = ?", (o["id"],))
            await db.commit()
    return {"reconciled": len(old_orders)}
