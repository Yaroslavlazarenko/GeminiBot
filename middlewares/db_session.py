from typing import Any, Awaitable, Callable, Optional
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as AiogramUser, Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.exc import SQLAlchemyError
import logging

from database.dao import UserDAO, GroupDAO, MessageHistoryDAO, StickerDAO, User as DBUser

logger = logging.getLogger(__name__)

class DAOMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory
        logger.info("DAOMiddleware (Separate DAOs approach) initialized.")

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: Optional[AiogramUser] = data.get("event_from_user")
        user_identifier = f"telegram_id={tg_user.id}" if tg_user else "Unknown User"

        if isinstance(event, Message):
            event_type = "Message"; event_id = event.message_id; chat_id = event.chat.id
        elif isinstance(event, CallbackQuery):
            event_type = "CallbackQuery"; event_id = event.id; chat_id = event.message.chat.id if event.message else "N/A"
        else:
            event_type = type(event).__name__; event_id = getattr(event, 'id', 'N/A'); chat_id = getattr(event, 'chat_id', 'N/A')
        logger.debug(f"Processing {event_type} (ID:{event_id}, Chat:{chat_id}) for {user_identifier}")

        async with self.session_factory() as session:
            user_dao = UserDAO(session)
            group_dao = GroupDAO(session)
            message_dao = MessageHistoryDAO(session)
            sticker_dao = StickerDAO(session)

            data["user_dao"] = user_dao
            data["group_dao"] = group_dao
            data["message_dao"] = message_dao
            data["sticker_dao"] = sticker_dao
            data["session_factory"] = self.session_factory

            db_user: Optional[DBUser] = None

            try:
                if tg_user:
                    logger.debug(f"Attempting get_or_create for {user_identifier} using UserDAO")
                    db_user = await user_dao.get_or_create_user(
                        telegram_id=tg_user.id,
                        username=tg_user.username or str(tg_user.id),
                        first_name=tg_user.first_name,
                        last_name=tg_user.last_name,
                    )
                    data["user"] = db_user
                    logger.debug(f"DB User object (ID: {db_user.id if db_user else 'N/A'}) ready via middleware for {user_identifier}")
                else:
                    data["user"] = None
                    logger.debug("No event_from_user found, skipping DB user steps.")

                logger.debug(f"Executing handler for {event_type} (ID:{event_id})")
                result = await handler(event, data)

                await session.commit()
                logger.debug(f"Handler finished successfully, session committed for {event_type} (ID:{event_id}) from {user_identifier}.")
                return result

            except SQLAlchemyError as db_err:
                logger.error(f"Database error for {event_type} (ID:{event_id}) from {user_identifier}: {db_err}", exc_info=True)
                await session.rollback()
                logger.warning(f"Session rolled back for {event_type} (ID:{event_id}) from {user_identifier} due to DB error.")
                error_message = "Виникла помилка при роботі з базою даних. Спробуйте пізніше."
                try:
                    if isinstance(event, Message): await event.answer(error_message)
                    elif isinstance(event, CallbackQuery) and event.message: await event.message.answer(error_message)
                except Exception as send_error: logger.error(f"Failed to send DB error message to user {user_identifier}: {send_error}")
                return None

            except Exception as e:
                logger.error(f"Handler error for {event_type} (ID:{event_id}) from {user_identifier}: {e}", exc_info=True)
                await session.rollback()
                logger.warning(f"Session rolled back for {event_type} (ID:{event_id}) from {user_identifier} due to handler error.")
                error_message = "Виникла внутрішня помилка. Спробуйте пізніше."
                try:
                    if isinstance(event, Message): await event.answer(error_message)
                    elif isinstance(event, CallbackQuery) and event.message: await event.message.answer(error_message)
                except Exception as send_error: logger.error(f"Failed to send error message to user {user_identifier}: {send_error}")
                return None