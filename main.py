import asyncio
import logging
import signal
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from core.config import Config
from bot.handlers import router as main_router
from bot.middlewares import DatabaseMiddleware, DeduplicationMiddleware
from core.database import DatabaseManager
from core.logger import setup_logging
from bot.web_admin import setup_admin_app

# Global objects
dp = None
bot = None
db_manager = None
runner = None

async def shutdown():
    """Graceful shutdown"""
    logger = logging.getLogger(__name__)
    logger.info("Starting shutdown...")
    
    try:
        if runner:
            logger.info("Stopping web admin server...")
            await runner.cleanup()
            
        if dp:
            logger.info("Stopping dispatcher...")
            try:
                await dp.stop_polling()
            except RuntimeError as e:
                if "Polling is not started" in str(e):
                    logger.info("Polling was not started, skipping stop_polling")
                else:
                    raise
        
        if bot:
            logger.info("Closing bot session...")
            await bot.session.close()
        
        if db_manager:
            logger.info("Closing database connections...")
            await db_manager.close()
            
    except Exception as e:
        logger.error(f"Error during shutdown: {e}", exc_info=True)
    finally:
        logger.info("Shutdown complete")

def signal_handler(signum, frame):
    """Handle termination signals"""
    logger = logging.getLogger(__name__)
    sig_name = signal.Signals(signum).name
    logger.info(f"Received signal {sig_name}")
    
    if asyncio.get_event_loop().is_running():
        asyncio.create_task(shutdown())

async def main():
    global dp, bot, db_manager, runner
    
    # Setup logging first
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting bot...")
    
    try:
        # Load configuration
        config = Config()
        
        # Initialize MongoDB
        db_manager = DatabaseManager(
            uri=config.mongo_uri,
            db_name=config.mongo_db_name
        )
        await db_manager.connect()
        
        # Initialize bot and dispatcher
        bot = Bot(
            token=config.bot_token,
            default=DefaultBotProperties(parse_mode="HTML")
        )
        dp = Dispatcher(storage=MemoryStorage())
        
        # Register middlewares (Dedup MUST come first to drop duplicates before any processing)
        dedup = DeduplicationMiddleware(ttl_seconds=120)
        dp.message.middleware(dedup)
        dp.callback_query.middleware(dedup)
        dp.message_reaction.middleware(dedup)
        
        dp.message.middleware(DatabaseMiddleware(db_manager))
        dp.callback_query.middleware(DatabaseMiddleware(db_manager))
        dp.message_reaction.middleware(DatabaseMiddleware(db_manager))
        
        # Include main router
        dp.include_router(main_router)
        
        # Setup and start web admin panel
        admin_app = setup_admin_app(db_manager, config, bot)
        runner = web.AppRunner(admin_app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', config.admin_port)
        await site.start()
        logger.info(f"Admin Panel running on http://0.0.0.0:{config.admin_port}")
            
        # Trigger initial sticker sync
        from services.sticker_service import StickerService
        from core.key_manager import get_key_manager
        settings = await db_manager.get_system_settings()
        packs_raw = settings.get("sticker_set_names") or settings.get("sticker_set_name") or "Animals"
        pack_names = [p.strip() for p in packs_raw.split(',') if p.strip()]
        asyncio.create_task(StickerService.sync_sticker_packs(bot, db_manager, get_key_manager(), pack_names))
        
        # Start polling
        logger.info("Bot is polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        raise
    finally:
        await shutdown()

if __name__ == "__main__":
    # Register signal handlers
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, signal_handler)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Bot stopped by user")
    except Exception as e:
        logging.getLogger(__name__).critical(f"Unexpected error: {e}", exc_info=True)