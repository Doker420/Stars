import aiosqlite
import json
import logging
import os
from contextlib import asynccontextmanager
from app.config import config

logger = logging.getLogger(__name__)

@asynccontextmanager
async def get_db():
    conn = await aiosqlite.connect(config.DATABASE_PATH, timeout=60.0)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA busy_timeout=60000;")
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA synchronous=NORMAL;")
    await conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
    finally:
        await conn.close()

async def init_db():
    async with get_db() as db:
        # 1. Users table
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            balance REAL DEFAULT 0.0,
            stars_balance REAL DEFAULT 0.0,
            spins_count INTEGER DEFAULT 1,
            avatar_url TEXT,
            ref_code TEXT UNIQUE NOT NULL,
            referred_by INTEGER,
            custom_ref_percent REAL DEFAULT NULL,
            is_banned INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (referred_by) REFERENCES users(telegram_id)
        )
        """)

        try:
            await db.execute("ALTER TABLE users ADD COLUMN stars_balance REAL DEFAULT 0.0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN spins_count INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT")
        except Exception:
            pass
        
        # 2. Orders table
        await db.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_uid TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL, -- 'stars' or 'premium'
            item_amount INTEGER NOT NULL,
            recipient_username TEXT NOT NULL,
            cost_rub REAL NOT NULL,
            cost_usdt REAL NOT NULL,
            cost_ton REAL NOT NULL,
            base_cost_rub REAL NOT NULL,
            margin_rub REAL NOT NULL,
            provider TEXT NOT NULL, -- 'greengame' or 'mystars'
            provider_order_id TEXT,
            status TEXT DEFAULT 'pending', -- pending, processing, completed, failed, refunded, reversed, expired
            payment_method TEXT NOT NULL,
            payment_id TEXT,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """)
        
        # 3. Transactions table
        await db.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL, -- deposit, deposit_pending, purchase, ref_reward, refund, task_reward, wheel_win
            amount_rub REAL NOT NULL,
            stars_amount REAL DEFAULT 0.0,
            description TEXT,
            order_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """)

        try:
            await db.execute("ALTER TABLE transactions ADD COLUMN stars_amount REAL DEFAULT 0.0")
        except Exception:
            pass
        
        # 4. Promo codes table
        await db.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            type TEXT NOT NULL,
            value REAL NOT NULL,
            max_uses INTEGER DEFAULT 100,
            current_uses INTEGER DEFAULT 0,
            owner_user_id INTEGER DEFAULT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # 5. Promo code redemptions
        await db.execute("""
        CREATE TABLE IF NOT EXISTS promo_redemptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promo_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            redeemed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (promo_id) REFERENCES promo_codes(id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(promo_id, user_id)
        )
        """)

        # 6. Tasks table
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_id TEXT NOT NULL,
            status TEXT DEFAULT 'available', -- 'available', 'completed'
            completed_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, task_id)
        )
        """)

        # 7. Wheel spins history
        await db.execute("""
        CREATE TABLE IF NOT EXISTS wheel_spins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            reward_type TEXT NOT NULL, -- 'stars', 'rub', 'discount', 'spin'
            reward_value REAL NOT NULL,
            reward_label TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """)
        
        # 8. Settings table
        await db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """)
        
        # 9. Audit log
        await db.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            action TEXT NOT NULL,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        # Seed default system settings
        default_settings = {
            "stars_base_rate_rub": "1.45",
            "stars_markup_percent": "14.0",
            "premium_3m_base_rub": "890.0",
            "premium_6m_base_rub": "1420.0",
            "premium_12m_base_rub": "2490.0",
            "premium_markup_percent": "12.0",
            "active_provider": "mystars",
            "payment_cryptobot_enabled": "1",
            "payment_yookassa_enabled": "1",
            "payment_yoomoney_enabled": "1",
            "payment_balance_enabled": "1",
            "ref_default_percent": "5.0",
            "site_maintenance": "0",
            "site_announcement": "⚡ Быстрая выдача Telegram Stars & Premium за 10-30 секунд. Автоматическая доставка по TON!",
            "site_theme": "telegram_dark",
            "ton_usdt_rate": "3.20",
            "usdt_rub_rate": "92.5",
            "telegram_channel_url": "https://t.me/starvault_news",
            "telegram_channel_id": "@starvault_news",
            "task_channel_url": "https://t.me/starvault_news",
            "task_channel_id": "@starvault_news",
            "task_channel_reward": "50",
            "task_channel_enabled": "1"
        }
        
        for k, v in default_settings.items():
            await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
            
        # Seed default promo codes
        await db.execute("""
        INSERT OR IGNORE INTO promo_codes (code, type, value, max_uses, current_uses, is_active)
        VALUES 
        ('START100', 'balance', 100.0, 500, 0, 1),
        ('STARS5', 'discount', 5.0, 1000, 0, 1),
        ('PREMIUM10', 'discount', 10.0, 500, 0, 1),
        ('PARTNER_PROMO', 'discount', 7.0, 9999, 0, 1)
        """)

        # Seed initial admin user if not existing
        admin_tg = config.ADMIN_TELEGRAM_IDS[0] if config.ADMIN_TELEGRAM_IDS else 123456789
        await db.execute("""
        INSERT OR IGNORE INTO users (id, telegram_id, username, first_name, balance, stars_balance, spins_count, ref_code, is_admin)
        VALUES (1, ?, 'admin', 'Администратор', 0.0, 0.0, 1, 'ADMIN01', 1)
        """, (admin_tg,))

        await db.commit()
        logger.info("Database initialized successfully.")
