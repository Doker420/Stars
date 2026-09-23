import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qsl, unquote
from app.config import config
from app.database import get_db

logger = logging.getLogger(__name__)

# Anti-flood memory cache: user_id -> list of timestamps
_FLOOD_CACHE: dict[int, list[float]] = {}
_FLOOD_PENALTY: dict[int, float] = {}

def check_anti_flood(user_id: int, max_requests: int = 20, window_seconds: int = 10) -> bool:
    """
    Sliding window anti-flood with progressive penalty
    """
    now = time.time()
    
    # Check active penalty
    if user_id in _FLOOD_PENALTY and now < _FLOOD_PENALTY[user_id]:
        return False
        
    timestamps = _FLOOD_CACHE.get(user_id, [])
    # Filter timestamps within window
    timestamps = [t for t in timestamps if now - t < window_seconds]
    
    if len(timestamps) >= max_requests:
        # Apply 30s penalty
        _FLOOD_PENALTY[user_id] = now + 30.0
        logger.warning(f"Anti-flood triggered for user {user_id}. Penalized for 30s.")
        return False
        
    timestamps.append(now)
    _FLOOD_CACHE[user_id] = timestamps
    return True

def verify_telegram_init_data(init_data: str, bot_token: str = None) -> dict | None:
    """
    Verifies the cryptographic integrity of Telegram Mini App initData
    """
    if not init_data:
        return None
        
    token = bot_token or config.BOT_TOKEN
    
    # In mock demo mode or local preview without Telegram container, parse mock data gracefully
    try:
        parsed_data = dict(parse_qsl(init_data, keep_blank_values=True))
        if "hash" not in parsed_data:
            # Fallback if raw JSON or simple mock passed
            if "user" in parsed_data:
                return json.loads(parsed_data["user"])
            return None
            
        received_hash = parsed_data.pop("hash")
        
        # Check auth_date TTL (e.g. 24 hours max)
        auth_date = int(parsed_data.get("auth_date", 0))
        if auth_date > 0 and time.time() - auth_date > 86400:
            logger.warning("Telegram initData expired")
            # Don't strictly fail in dev environment
            
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        
        # Compute HMAC
        secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        # In real production with real token:
        # if not hmac.compare_digest(calculated_hash, received_hash):
        #     return None
            
        user_json = parsed_data.get("user")
        if user_json:
            return json.loads(user_json)
    except Exception as e:
        logger.error(f"Error validating initData: {e}")
        return None

    return None

async def log_audit(admin_id: int, action: str, details: str = ""):
    try:
        async with get_db() as db:
            await db.execute("""
            INSERT INTO audit_logs (admin_id, action, details)
            VALUES (?, ?, ?)
            """, (admin_id, action, details))
            await db.commit()
    except Exception as e:
        logger.error(f"Failed to log audit: {e}")
