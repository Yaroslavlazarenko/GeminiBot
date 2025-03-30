import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher

from config import Config
from telegram.handlers.response_handlers import router

from middlewares.database_middleware import DAOMiddleware
from services.database.manager import DatabaseManager

async def main():
    config = Config()

    bot = Bot(token=config.bot_token)

    database_manager = DatabaseManager(
        user=config.db_user,
        password=config.db_password,
        host=config.db_host,
        db_name=config.db_name,
    )


    await database_manager.create_database()
    await database_manager.create_tables()

    dispatcher = Dispatcher(bot=bot)
    dispatcher.include_router(router)
    dispatcher.message.middleware(DAOMiddleware(database_manager=database_manager))

    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    await dispatcher.start_polling(bot)

asyncio.run(main())