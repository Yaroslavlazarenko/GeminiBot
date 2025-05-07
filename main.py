import asyncio
import logging
import signal
from typing import Set, Optional
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
dp: Optional[Dispatcher] = None
SHUTDOWN_TIMEOUT = 10  # seconds

async def handle_polling(dp: Dispatcher, bot: Bot) -> None:
    """Handle long polling with automatic reconnection."""
    try:
        while not shutdown_event.is_set():
            try:
                await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
            except TelegramNetworkError as e:
                if not shutdown_event.is_set():  # Only log if we're not shutting down
                    logger.error(f"Telegram connection error: {e}. Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                logger.info("Polling task cancelled")
                raise
            except Exception as e:
                if not shutdown_event.is_set():  # Only log if we're not shutting down
                    logger.error(f"Unexpected error in polling: {e}", exc_info=True)
                    await asyncio.sleep(5)
    finally:
        logger.info("Polling stopped")

async def cleanup(stack: AsyncExitStack, bot: Bot, db_manager: DatabaseManager) -> None:
    """Cleanup resources with timeout."""
    logger.info("Starting cleanup...")
    try:
        # Cancel all remaining tasks
        remaining_tasks = [t for t in active_tasks if not t.done()]
        if remaining_tasks:
            logger.info(f"Cancelling {len(remaining_tasks)} remaining tasks...")
            for task in remaining_tasks:
                task.cancel()
            
            # Wait for tasks to cancel with timeout
            try:
                await asyncio.wait_for(asyncio.gather(*remaining_tasks, return_exceptions=True), timeout=SHUTDOWN_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning(f"Some tasks did not complete within {SHUTDOWN_TIMEOUT} seconds")

        # Close resources with timeout
        try:
            await asyncio.wait_for(stack.aclose(), timeout=SHUTDOWN_TIMEOUT/2)
            await asyncio.wait_for(bot.session.close(), timeout=SHUTDOWN_TIMEOUT/2)
            await asyncio.wait_for(db_manager.dispose_engine(), timeout=SHUTDOWN_TIMEOUT/2)
        except asyncio.TimeoutError:
            logger.warning("Resource cleanup timed out")
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

            global dp  # Make dispatcher globally accessible for shutdown
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
            # Set shutdown event and initiate cleanup
            shutdown_event.set()
            if dp:
                await dp.stop_polling()  # Stop polling properly
            await cleanup(stack, bot, db_manager)

async def shutdown(signame: str) -> None:
    """Handle shutdown signals asynchronously."""
    logger.critical(f"Received {signame}, initiating shutdown...")
    shutdown_event.set()
    if dp:
        await dp.stop_polling()

def handle_signal(signame: str) -> None:
    """Convert sync signal to async shutdown."""
    logger.info(f"Received signal {signame}")
    asyncio.create_task(shutdown(signame))

if __name__ == "__main__":
    logger = logging.getLogger(__name__)
    
    # Register signal handlers
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda signum, _: handle_signal(signal.Signals(signum).name))
    
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.critical("Bot stopped by user.")
    except Exception as e:
        logger.critical(f"Unhandled exception in main: {e}", exc_info=True)