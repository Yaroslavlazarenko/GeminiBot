import asyncio
import logging
import signal
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from config import Config
from telegram.handlers import all_routers
from middlewares.db_session import DAOMiddleware
from database.manager import DatabaseManager
from logging_config import setup_logging

# Global objects
dp = None
bot = None
db_manager = None

async def shutdown():
    """Graceful shutdown"""
    logger = logging.getLogger(__name__)
    logger.info("Starting shutdown...")
    
    try:
        if dp:
            logger.info("Stopping dispatcher...")
            await dp.stop_polling()
        
        if bot:
            logger.info("Closing bot session...")
            session = await bot.get_session()
            if session:
                await session.close()
        
        if db_manager:
            logger.info("Closing database connections...")
            await db_manager.dispose_engine()
            
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
    global dp, bot, db_manager
    
    # Setup logging first
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting bot...")
    
    try:
        # Load configuration
        config = Config()  # This will load from .env file
        
        # Initialize database with configuration
        db_manager = DatabaseManager(
            user=config.db_user,
            password=config.db_password,
            host=config.db_host,
            db_name=config.db_name
        )
        await db_manager.initialize()
        
        # Initialize bot and dispatcher with new DefaultBotProperties
        bot = Bot(
            token=config.bot_token,
            default=DefaultBotProperties(parse_mode="HTML")
        )
        dp = Dispatcher(storage=MemoryStorage())
        
        # Register middlewares
        session_factory = db_manager.get_session_factory()
        dp.message.middleware(DAOMiddleware(session_factory))
        dp.callback_query.middleware(DAOMiddleware(session_factory))
        
        # Include all routers
        for router in all_routers:
            dp.include_router(router)
            
        # Start polling
        logger.info("Bot is running...")
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