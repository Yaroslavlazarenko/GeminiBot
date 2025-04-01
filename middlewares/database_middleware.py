# middlewares/database_middleware.py

from typing import Any, Awaitable, Callable, Optional
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as AiogramUser, Message, CallbackQuery # Добавим типы Event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.exc import SQLAlchemyError
import logging

from services.database.dao import DAO
# Убираем импорт DatabaseManager, он не нужен в middleware
from services.database.models import User as DBUser # Импортируем нашу модель User

logger = logging.getLogger(__name__)

class DAOMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Получаем пользователя Aiogram
        tg_user: Optional[AiogramUser] = data.get("event_from_user")
        # Формируем идентификаторы для логов
        user_identifier = f"telegram_id={tg_user.id}" if tg_user else "Unknown User"
        if isinstance(event, Message):
            event_type = "Message"
            event_id = event.message_id
            chat_id = event.chat.id
        elif isinstance(event, CallbackQuery):
            event_type = "CallbackQuery"
            event_id = event.id
            chat_id = event.message.chat.id if event.message else "N/A"
        else:
            event_type = type(event).__name__
            event_id = getattr(event, 'id', 'N/A')
            chat_id = getattr(event, 'chat_id', 'N/A')

        logger.debug(f"Processing {event_type} (ID:{event_id}, Chat:{chat_id}) for {user_identifier}")

        async with self.session_factory() as session:
            dao = DAO(session)
            data["dao"] = dao
            db_user: Optional[DBUser] = None
            is_new_user = False # Флаг для логгирования

            if tg_user:
                try:
                    db_user = await dao.get_user_by_telegram_id(tg_user.id)

                    if not db_user:
                        logger.info(f"User {user_identifier} not found in DB, creating...")
                        db_user = await dao.create_user( # Создаем и добавляем в сессию
                            username=tg_user.username or str(tg_user.id),
                            telegram_id=tg_user.id,
                            first_name=tg_user.first_name,
                            last_name=tg_user.last_name,
                        )
                        is_new_user = True
                        # НЕ делаем commit здесь! Новый пользователь сохранится вместе с остальными изменениями.
                    else:
                         logger.debug(f"User {user_identifier} found in DB (ID={db_user.id})")

                except SQLAlchemyError as e:
                    # Ошибка при поиске/создании - критично
                    logger.exception(f"CRITICAL: DB error getting/creating user {user_identifier} in middleware", exc_info=e)
                    # Можно попытаться ответить пользователю, но это сложно сделать универсально
                    return None # Прерываем обработку

            data["user"] = db_user # Передаем объект DBUser (или None, если tg_user не было)

            try:
                # Вызываем следующий хендлер
                result = await handler(event, data)

                # Если хендлер успешно отработал, коммитим ВСЕ изменения в сессии
                # (включая нового пользователя, добавленные сообщения, измененные настройки)
                await session.commit()
                log_msg = f"Handler finished for {event_type} (ID:{event_id}) from {user_identifier}, session committed."
                if is_new_user and db_user:
                    log_msg += f" New user created with DB ID={db_user.id}."
                logger.debug(log_msg)

                return result # Возвращаем результат хендлера

            except Exception as e:
                # Ловим ЛЮБУЮ ошибку из хендлера или при коммите
                logger.error(
                    f"Error during handler or final commit for {event_type} (ID:{event_id}) from {user_identifier}: {e}",
                    exc_info=True
                )
                await session.rollback()
                logger.warning(f"Session rolled back for {event_type} (ID:{event_id}) from {user_identifier} due to error.")
                # Не перевыбрасываем ошибку по умолчанию, чтобы бот не падал на каждом чихе,
                # но можно добавить логику ответа пользователю об ошибке, если это Message или CallbackQuery
                error_message = "Виникла внутрішня помилка. Спробуйте пізніше."
                try:
                    if isinstance(event, Message):
                        await event.answer(error_message)
                    elif isinstance(event, CallbackQuery) and event.message:
                        await event.message.answer(error_message)
                except Exception as send_error:
                     logger.error(f"Failed to send error message to user {user_identifier}: {send_error}")
                # Можно и перевыбросить, если нужно остановить обработку на уровне Aiogram
                # raise e
                return None # Просто завершаем обработку этого события