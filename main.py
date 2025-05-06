import asyncio
import logging
import signal
from typing import Set
from contextlib import AsyncExitStack
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramNetworkError

from config import Config
from telegram.handlers import all_routers
from middlewares.db_session import DAOMiddleware
from database.manager import DatabaseManager
from logging_config import setup_logging

# Global objects for graceful shutdown
active_tasks: Set[asyncio.Task] = set()
shutdown_event: asyncio.Event = asyncio.Event()

async def handle_polling(dp: Dispatcher, bot: Bot) -> None:
    """Handle long polling with automatic reconnection."""
    while not shutdown_event.is_set():
        try:
            await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
        except TelegramNetworkError as e:
            logger.error(f"Telegram connection error: {e}. Reconnecting in 5 seconds...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Unexpected error in polling: {e}", exc_info=True)
            await asyncio.sleep(5)

async def cleanup(stack: AsyncExitStack, bot: Bot, db_manager: DatabaseManager) -> None:
    """Cleanup resources."""
    logger.info("Starting cleanup...")
    try:
        await stack.aclose()  # This will close resources in reverse order
        await bot.session.close()
        await db_manager.dispose_engine()
    except Exception as e:
        logger.error(f"Error during cleanup: {e}", exc_info=True)
    finally:
        logger.info("Cleanup completed")

async def main():
    # Setup logging
    logger = setup_logging()
    logger.critical("Starting bot...")

    async with AsyncExitStack() as stack:
        try:
            config = Config()
            db_manager = DatabaseManager(
                user=config.db_user,
                password=config.db_password,
                host=config.db_host,
                db_name=config.db_name,
            )
            
            # Initialize database manager
            await db_manager.initialize()
            await stack.enter_async_context(db_manager)
            
            await db_manager.create_database()
            await db_manager.create_tables()

            session_factory = db_manager.get_session_factory()
            storage = MemoryStorage()
            bot = Bot(token=config.bot_token)
            await stack.enter_async_context(bot)

            dp = Dispatcher(storage=storage)
            dp.update.middleware(DAOMiddleware(session_factory=session_factory))
            logger.info("Database middleware registered")

            dp.include_routers(*all_routers)
            logger.info("Response handlers router included")

            # Delete webhook and start polling in the background
            await bot.delete_webhook(drop_pending_updates=True)
            polling_task = asyncio.create_task(handle_polling(dp, bot))
            active_tasks.add(polling_task)
            polling_task.add_done_callback(active_tasks.discard)

            # Wait for shutdown signal
            await shutdown_event.wait()
            
        except Exception as e:
            logger.critical(f"Fatal error: {e}", exc_info=True)
            raise
        finally:
            # Set shutdown event and wait for tasks to complete
            shutdown_event.set()
            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)
            await cleanup(stack, bot, db_manager)

def handle_shutdown(signame: str) -> None:
    """Handle shutdown signals."""
    logger.critical(f"Received {signame}, initiating shutdown...")
    shutdown_event.set()

if __name__ == "__main__":
    logger = logging.getLogger(__name__)
    
    # Register signal handlers
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda signum, _: handle_shutdown(signal.Signals(signum).name))
    
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.critical("Bot stopped by user.")
    except Exception as e:
        logger.critical(f"Unhandled exception in main: {e}", exc_info=True)