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
        logger.info("DAOMiddleware initialized with optimized session handling")

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

        session = None
        try:
            logger.debug(f"Starting DB session for {event_type} (ID:{event_id}, Chat:{chat_id}) from {user_identifier}")
            session = self.session_factory()

            # Initialize all DAOs with the same session
            data.update({
                "user_dao": UserDAO(session),
                "group_dao": GroupDAO(session),
                "message_dao": MessageHistoryDAO(session),
                "sticker_dao": StickerDAO(session),
                "session_factory": self.session_factory
            })

            # Handle user creation/retrieval
            if tg_user:
                try:
                    async with session.begin_nested():
                        db_user = await data["user_dao"].get_or_create_user(
                            telegram_id=tg_user.id,
                            username=tg_user.username or str(tg_user.id),
                            first_name=tg_user.first_name,
                            last_name=tg_user.last_name,
                        )
                    data["user"] = db_user
                    logger.debug(f"User object ready (ID: {db_user.id if db_user else 'N/A'}) for {user_identifier}")
                except SQLAlchemyError as e:
                    logger.error(f"Error getting/creating user {user_identifier}: {e}")
                    raise
            else:
                data["user"] = None
                logger.debug(f"No user information for {event_type} (ID:{event_id})")

            # Execute handler within a transaction
            async with session.begin():
                logger.debug(f"Executing handler for {event_type} (ID:{event_id})")
                result = await handler(event, data)
                return result

        except SQLAlchemyError as db_err:
            logger.error(f"Database error for {event_type} (ID:{event_id}) from {user_identifier}: {db_err}", exc_info=True)
            error_message = "База даних тимчасово недоступна. Спробуйте пізніше."
            await self._send_error_message(event, error_message)
            return None

        except Exception as e:
            logger.error(f"Handler error for {event_type} (ID:{event_id}) from {user_identifier}: {e}", exc_info=True)
            error_message = "Виникла внутрішня помилка. Спробуйте пізніше."
            await self._send_error_message(event, error_message)
            return None

        finally:
            if session:
                try:
                    await session.close()
                    logger.debug(f"Closed DB session for {event_type} (ID:{event_id}) from {user_identifier}")
                except Exception as e:
                    logger.error(f"Error closing session for {event_type} (ID:{event_id}): {e}")

    async def _send_error_message(self, event: TelegramObject, message: str) -> None:
        """Helper method to send error messages to users."""
        try:
            if isinstance(event, Message):
                await event.answer(message)
            elif isinstance(event, CallbackQuery) and event.message:
                await event.message.answer(message)
        except Exception as e:
            logger.error(f"Failed to send error message: {e}")