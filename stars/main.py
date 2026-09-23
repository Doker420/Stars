import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.config import config
from app.database import init_db
from app.web.routes_api import router as api_router
from app.web.routes_admin import router as admin_router
from app.web.routes_webhook import router as webhook_router, sync_pending_orders
from app.web.routes_gamification import router as gamification_router
from app.bot.handlers import router as bot_router

# Aiogram setup
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("store_app")

async def background_order_reconciler():
    """Background worker reconciling pending orders every 60 seconds"""
    while True:
        try:
            await asyncio.sleep(60)
            await sync_pending_orders()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in background reconciler: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Telegram Stars & Premium Store Engine...")
    await init_db()
    
    # Start background order reconciler
    bg_task = asyncio.create_task(background_order_reconciler())
    
    # Start Aiogram bot in background if valid token provided
    bot_task = None
    if config.BOT_TOKEN and not config.BOT_TOKEN.startswith("7777777777") and not config.BOT_TOKEN.startswith("1234567890"):
        try:
            bot = Bot(token=config.BOT_TOKEN)
            dp = Dispatcher(storage=MemoryStorage())
            dp.include_router(bot_router)
            bot_task = asyncio.create_task(dp.start_polling(bot))
            logger.info("Aiogram 3 Telegram bot polling started successfully.")
        except Exception as e:
            logger.warning(f"Could not start Telegram Bot polling: {e}")
    else:
        logger.info("Running in Web / Mini App preview mode (Aiogram bot polling inactive until real BOT_TOKEN provided in .env).")

    yield
    
    # Shutdown
    bg_task.cancel()
    if bot_task:
        bot_task.cancel()
    logger.info("Store engine stopped.")

app = FastAPI(
    title="StarVault - Telegram Stars & Premium Store API",
    version="3.1.0",
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Anti-Cache Middleware for WebApp / Mini App assets
@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response: Response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/" or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "app", "web", "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Include Routers
app.include_router(api_router)
app.include_router(admin_router)
app.include_router(webhook_router)
app.include_router(gamification_router)

@app.get("/")
async def root_index():
    index_file = os.path.join(static_dir, "index.html")
    return FileResponse(
        index_file,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )

@app.get("/health")
async def health_check():
    return {"status": "ok", "app": "StarVault Store", "version": "3.1.0"}

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        reload=False
    )
