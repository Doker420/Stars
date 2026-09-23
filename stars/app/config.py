import os
from pydantic import BaseModel

class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMIN_TELEGRAM_IDS: list[int] = [int(x.strip()) for x in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",") if x.strip()]
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-to-a-very-long-random-string-64-chars")
    WEB_APP_URL: str = os.getenv("WEB_APP_URL", "https://stats-max.ru")


     # MyStars Fulfilment API (https://mystars.tg/docs)
    MYSTARS_API_KEY: str = os.getenv("MYSTARS_API_KEY", os.getenv("MYSTARS_FAAS_KEY", ""))
    MYSTARS_BASE_URL: str = os.getenv("MYSTARS_BASE_URL", "https://api.mystars.tg")
    MYSTARS_WEBHOOK_SECRET: str = os.getenv("MYSTARS_WEBHOOK_SECRET", "")
    MYSTARS_PAYMENT_CURRENCY: str = os.getenv("MYSTARS_PAYMENT_CURRENCY", "ton") # 'ton' or 'usdt_ton'

       
    # Provider keys
    GREENGAME_API_KEY: str = os.getenv("GREENGAME_API_KEY", "demo_gg_key")
    TON_WALLET_MNEMONIC: str = os.getenv("TON_WALLET_MNEMONIC", "demo tone mnemonic words twenty four words total for testing sandbox environment")
    
    # Payments
    CRYPTOBOT_TOKEN: str = os.getenv("CRYPTOBOT_TOKEN", "")
    YOOKASSA_SHOP_ID: str = os.getenv("YOOKASSA_SHOP_ID", "demo_shop_id")
    YOOKASSA_SECRET_KEY: str = os.getenv("YOOKASSA_SECRET_KEY", "demo_yookassa_secret")
    YOOMONEY_RECEIVER: str = os.getenv("YOOMONEY_RECEIVER", "410010000000000")
    YOOMONEY_SECRET: str = os.getenv("YOOMONEY_SECRET", "demo_yoomoney_secret")
    
    # DB
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "/home/telegram_stars_store/store.db")
    
    # Host & Port
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "5000"))

config = Config()
