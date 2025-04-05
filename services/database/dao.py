# services/database/dao.py
import logging
from typing import Optional, List
from datetime import datetime, timezone

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
# Import the postgresql dialect specific insert statement
from sqlalchemy.dialects.postgresql import insert as pg_insert

from services.database.models import MessageHistory, User, MessageRole
from google.genai import types

logger = logging.getLogger(__name__)

class DAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    # --- WRITE METHODS ---

    async def get_or_create_user(self, telegram_id: int, **kwargs) -> User:
        """
        Gets a user by telegram_id or creates them if they don't exist.
        Uses INSERT ... ON CONFLICT DO NOTHING for atomicity.
        kwargs: username, first_name, last_name, etc.
        """
        logger.debug(f"Attempting to get or create user for telegram_id={telegram_id}")

        # Prepare the insert statement with ON CONFLICT DO NOTHING
        # This attempts the insert, but if a user with that telegram_id
        # already exists (violates unique constraint), it does nothing.
        insert_stmt = pg_insert(User).values(
            telegram_id=telegram_id,
            **kwargs # Pass username, first_name, etc.
        ).on_conflict_do_nothing(
            index_elements=['telegram_id'] # Specify the constraint column(s)
        )

        try:
            # Execute the potential insert
            await self.session.execute(insert_stmt)
            # We don't need to flush here specifically for the insert,
            # commit at the end of the middleware request will handle it.
            # If the insert happened, it's now pending in the transaction.
            # If it didn't happen (due to conflict), nothing was added.

            # --- IMPORTANT: Always fetch the user afterwards ---
            # This ensures we get the user object, whether it pre-existed
            # or was just inserted by this call (or another concurrent call).
            user = await self.get_user_by_telegram_id(telegram_id)
            if user:
                logger.info(f"Successfully got or created user for telegram_id={telegram_id}, user_id={user.id}")
                return user
            else:
                # This case should theoretically be rare if the insert/get logic is correct
                # but handle defensively. Could happen if commit fails later.
                logger.error(f"Failed to get user for telegram_id={telegram_id} after insert attempt.")
                raise RuntimeError(f"Could not get or create user {telegram_id}") # Or return None/raise specific exception

        except SQLAlchemyError as e:
            # Log the specific error during the get_or_create process
            logger.error(f"Database error during get_or_create_user for telegram_id={telegram_id}: {e}", exc_info=True)
            # Reraise the exception to be handled by the middleware (rollback)
            raise

    # --- DEPRECATE create_user or make it internal ---
    # async def create_user(...) -> This function is now effectively replaced by get_or_create_user

    # ... (keep add_message, clear_history, get_message, get_user_by_telegram_id, get_user_messages_as_contents) ...

    async def add_message(self, user_id: int, role: MessageRole, text: str | None = None, audio_data: bytes | None = None, image_data: bytes | None = None, video_data: bytes | None = None) -> MessageHistory:
        """Добавляет экземпляр сообщения в сессию."""
        logger.debug(f"Adding message for user_id={user_id}, role={role.value}")
        new_message = MessageHistory(
            user_id=user_id,
            role=role.value, # Use the enum value directly if the column expects string
            text=text,
            audio_data=audio_data,
            image_data=image_data,
            video_data=video_data,
            timestamp=datetime.now(timezone.utc) # Keep timestamp generation here
        )
        self.session.add(new_message)
        # await self.session.flush([new_message]) # Flush is often not needed here if commit happens later
        logger.debug(f"Message for user_id={user_id} added to session.")
        return new_message

    async def clear_history(self, user_id: int) -> int:
        """Удаляет историю сообщений для пользователя. Возвращает кол-во удаленных строк."""
        logger.info(f"Clearing message history for user_id={user_id}")
        stmt = delete(MessageHistory).where(MessageHistory.user_id == user_id)
        result = await self.session.execute(stmt)
        deleted_count = result.rowcount
        logger.info(f"Cleared {deleted_count} messages for user_id={user_id}")
        return deleted_count

    async def get_message(self, message_id: int) -> Optional[MessageHistory]:
        """Получает сообщение по ID."""
        try:
            result = await self.session.get(MessageHistory, message_id)
            return result
        except SQLAlchemyError as e:
            logger.error(f"Error getting message by id={message_id}: {e}", exc_info=True)
            return None

    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """Получает пользователя по telegram_id."""
        try:
            stmt = select(User).where(User.telegram_id == telegram_id)
            result = await self.session.execute(stmt)
            user = result.scalar_one_or_none()
            if user:
                logger.debug(f"User found for telegram_id={telegram_id}")
            else:
                 logger.debug(f"User not found for telegram_id={telegram_id}")
            return user
        except SQLAlchemyError as e:
            logger.error(f"Error getting user by telegram_id={telegram_id}: {e}", exc_info=True)
            # Don't return None here, let the exception propagate up
            # so the middleware knows something went wrong during the fetch.
            raise # Or return None if middleware specifically handles None return on DB error

    async def get_user_messages_as_contents(self, user_id: int, limit: int = 50) -> List[types.Content]:
        """
        Получает последние сообщения пользователя и форматирует их для Gemini API.
        Обрабатывает несколько частей (parts) в одном сообщении.
        """
        # ... (implementation seems okay) ...
        contents: List[types.Content] = []
        try:
            stmt = (
                select(MessageHistory)
                .where(MessageHistory.user_id == user_id)
                .order_by(MessageHistory.timestamp.desc()) # Order descending first
                .limit(limit)
            )
            result = await self.session.execute(stmt)
            # Fetch all and reverse in Python is okay for moderate limits
            messages: List[MessageHistory] = list(result.scalars().all())
            messages.reverse() # Reverse to get chronological order

            logger.debug(f"Retrieved last {len(messages)} messages for user_id={user_id} to build contents")

            for message in messages:
                parts = []
                if message.text:
                    parts.append(types.Part.from_text(text=message.text))
                if message.audio_data:
                    parts.append(types.Part.from_bytes(data=message.audio_data, mime_type="audio/ogg")) # Adjust mime type if needed
                if message.image_data:
                    parts.append(types.Part.from_bytes(data=message.image_data, mime_type="image/jpeg")) # Adjust mime type if needed
                if message.video_data:
                    parts.append(types.Part.from_bytes(data=message.video_data, mime_type="video/mp4")) # Adjust mime type if needed

                if parts:
                    # Ensure role is 'user' or 'model' string
                    role_str = message.role.value if isinstance(message.role, MessageRole) else message.role
                    contents.append(types.Content(role=role_str, parts=parts))
                else:
                    logger.warning(f"Message id={message.id} for user_id={user_id} has no content parts, skipping.")

            return contents

        except SQLAlchemyError as e:
            logger.error(f"Error getting message history for user_id={user_id}: {e}", exc_info=True)
            return []