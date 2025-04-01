# main.py

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage # Или другое хранилище

from config import Config
from telegram.handlers import response_handlers # Импортируем роутер
from middlewares.database_middleware import DAOMiddleware
from services.database.manager import DatabaseManager

async def main():
    # --- Настройка логирования ---
    log_level = logging.INFO # Установите уровень DEBUG для подробной информации
    log_format = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    logging.basicConfig(level=log_level, stream=sys.stdout, format=log_format)
    logger = logging.getLogger(__name__)
    logger.info("Starting bot...")

    # --- Конфигурация ---
    config = Config()
    logger.info("Configuration loaded.")

    # --- База данных ---
    db_manager = DatabaseManager(
        user=config.db_user,
        password=config.db_password,
        host=config.db_host,
        db_name=config.db_name,
    )
    # Создание БД (если нет) и таблиц
    await db_manager.create_database()
    await db_manager.create_tables() # Выбросит исключение, если не удастся

    # Получаем фабрику сессий
    session_factory = db_manager.get_session_factory()

    # --- Aiogram ---
    # Используйте нужное вам хранилище FSM, если используете состояния
    storage = MemoryStorage()
    bot = Bot(token=config.bot_token)
    dp = Dispatcher(storage=storage) # Передаем storage в Dispatcher

    # --- Регистрация Middleware ---
    # Регистрируем наш middleware для всех обновлений, где есть 'event_from_user'
    # Это будет работать для Message, CallbackQuery и т.д., где Aiogram предоставляет юзера
    dp.update.middleware(DAOMiddleware(session_factory=session_factory))
    logger.info("Database middleware registered.")

    # --- Регистрация Роутеров ---
    dp.include_router(response_handlers.router)
    logger.info("Response handlers router included.")
    # Добавьте другие роутеры здесь, если они есть
    # dp.include_router(admin_handlers.router)

    # --- Запуск бота ---
    logger.info("Starting polling...")
    try:
        # Удаляем вебхук перед запуском поллинга (на всякий случай)
        await bot.delete_webhook(drop_pending_updates=True)
        # Запускаем поллинг
        await dp.start_polling(bot)
    finally:
        # Корректное завершение
        logger.info("Stopping bot...")
        await bot.session.close()
        await db_manager.dispose_engine() # Закрываем соединения с БД
        logger.info("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger(__name__).info("Bot stopped by user.")
    except Exception as e:
        # Логируем критические ошибки при запуске/работе main()
         logging.getLogger(__name__).critical(f"Unhandled exception in main: {e}", exc_info=True)