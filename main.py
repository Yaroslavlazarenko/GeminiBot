import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import Config
from telegram.handlers import all_routers
from middlewares.db_session import DAOMiddleware
from database.manager import DatabaseManager

async def main():
    log_level = logging.INFO
    log_format = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    logging.basicConfig(level=log_level, stream=sys.stdout, format=log_format)
    logger = logging.getLogger(__name__)
    logger.info("Starting bot...")

    config = Config()
    logger.info("Configuration loaded.")

    db_manager = DatabaseManager(
        user=config.db_user,
        password=config.db_password,
        host=config.db_host,
        db_name=config.db_name,
    )
    await db_manager.create_database()
    await db_manager.create_tables()

    session_factory = db_manager.get_session_factory()

    storage = MemoryStorage()
    bot = Bot(token=config.bot_token)
    dp = Dispatcher(storage=storage)

    dp.update.middleware(DAOMiddleware(session_factory=session_factory))
    logger.info("Database middleware registered.")

    dp.include_routers(*all_routers)
    logger.info("Response handlers router included.")

    logger.info("Starting polling...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        logger.info("Stopping bot...")
        await bot.session.close()
        await db_manager.dispose_engine()
        logger.info("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger(__name__).info("Bot stopped by user.")
    except Exception as e:
         logging.getLogger(__name__).critical(f"Unhandled exception in main: {e}", exc_info=True)