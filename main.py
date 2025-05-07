import asyncio
import logging
import signal
import sys
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import Config
from telegram.handlers import all_routers
from middlewares.db_session import DAOMiddleware
from database.manager import DatabaseManager
from logging_config import setup_logging

# Global objects for graceful shutdown
shutdown_event = asyncio.Event()
dp = None

async def shutdown():
    """Handle shutdown process."""
    global dp
    if dp:
        await dp.stop_polling()

def handle_signal(signum, frame):
    """Handle termination signals."""
    logger = logging.getLogger(__name__)
    logger.info(f"Received signal {signal.Signals(signum).name}")
    shutdown_event.set()

async def main():
    global dp
    # Setup logging
    logger = setup_logging()
    logger.critical("Starting bot...")

    config = Config()
    db_manager = DatabaseManager(
        user=config.db_user,
        password=config.db_password,
        host=config.db_host,
        db_name=config.db_name,
    )
    
    try:
        await db_manager.initialize()
        await db_manager.create_database()
        await db_manager.create_tables()

        session_factory = db_manager.get_session_factory()
        storage = MemoryStorage()
        bot = Bot(token=config.bot_token)
        dp = Dispatcher(storage=storage)

        dp.update.middleware(DAOMiddleware(session_factory=session_factory))
        logger.info("Database middleware registered")

        dp.include_routers(*all_routers)
        logger.info("Response handlers router included")

        await bot.delete_webhook(drop_pending_updates=True)
        
        # Start polling in the background
        polling_task = asyncio.create_task(dp.start_polling(bot))
        
        # Wait for shutdown signal
        await shutdown_event.wait()
        
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        raise
    finally:
        logger.critical("Stopping bot...")
        if dp:
            await dp.stop_polling()
        await bot.session.close()
        await db_manager.dispose_engine()
        logger.info("Bot stopped")

if __name__ == "__main__":
    logger = logging.getLogger(__name__)
    
    # Register signal handlers
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, handle_signal)
    
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.critical("Bot stopped by user.")
    except Exception as e:
        logger.critical(f"Unhandled exception in main: {e}", exc_info=True)
    finally:
        if sys.exc_info()[0] is not None:
            sys.exit(1)