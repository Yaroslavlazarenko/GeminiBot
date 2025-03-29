import asyncio
import logging
import sys

#from aiogram.fsm.storage.memory import MemoryStorage
from aiogram import Bot, Dispatcher

from config import Config
from telegram.handlers.response_handlers import router

async def main():
    config = Config()

    bot = Bot(token=config.bot_token)

    dispatcher = Dispatcher(bot=bot)
    dispatcher.include_router(router)

    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    await dispatcher.start_polling(bot)

asyncio.run(main())