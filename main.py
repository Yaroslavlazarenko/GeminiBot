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
bot = None
db_manager = None

async def shutdown(timeout=10):
    """Handle shutdown process with timeout."""
    global dp, bot, db_manager
    logger = logging.getLogger(__name__)
    
    try:
        async with asyncio.timeout(timeout):
            logger.info("Starting graceful shutdown...")
            
            if dp:
                logger.info("Stopping dispatcher...")
                await dp.stop_polling()
            
            if bot:
                logger.info("Closing bot session...")
                await bot.session.close()
            
            if db_manager:
                logger.info("Disposing database engine...")
                await db_manager.dispose_engine()
                
    except asyncio.TimeoutError:
        logger.error(f"Shutdown timed out after {timeout} seconds")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")

def handle_signal(signum, frame):
    """Handle termination signals by scheduling shutdown in event loop."""
    logger = logging.getLogger(__name__)
    sig_name = signal.Signals(signum).name
    logger.info(f"Received signal {sig_name}")
    
    # Schedule shutdown in the event loop
    if asyncio.get_event_loop().is_running():
        shutdown_event.set()
    else:
        logger.warning("Event loop not running during signal handling")

async def main():
    global dp, bot, db_manager
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
        
        # Perform graceful shutdown
        await shutdown()
        
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        raise
    finally:
        # Ensure shutdown is called even if an error occurred
        if not shutdown_event.is_set():
            await shutdown(timeout=5)  # Use shorter timeout in error case
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