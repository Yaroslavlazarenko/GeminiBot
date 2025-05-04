# services/database/message_dao.py
import logging
from typing import Optional, List
from datetime import datetime, timezone
import pytz

from sqlalchemy import select, delete, and_, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from google.genai import types
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..models import MessageHistory, MessageRole

logger = logging.getLogger(__name__)

class MessageHistoryDAO:
    """Асинхронный DAO для работы с моделью MessageHistory."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_message(
        self, 
        user_id: int, 
        role: MessageRole, 
        text: str | None = None, 
        audio_data: bytes | None = None, 
        image_data: bytes | None = None, 
        video_data: bytes | None = None, 
        group_id: int | None = None,
        telegram_message_id: int | None = None
    ) -> MessageHistory:
        new_message = MessageHistory(
            user_id=user_id,
            group_id=group_id,
            role=role,
            text=text,
            audio_data=audio_data,
            image_data=image_data,
            video_data=video_data,
            telegram_message_id=telegram_message_id,
            timestamp=datetime.now(timezone.utc)
        )
        self.session.add(new_message)
        return new_message

    async def clear_history(
        self,
        *, # Force keyword arguments for clarity
        user_id: int | None = None,
        group_id: int | None = None,
        clear_group_wide: bool = False,
        limit: int | None = None
    ) -> int:
        """Очищает историю сообщений по заданным критериям."""
        if clear_group_wide:
            if group_id is None:
                raise ValueError("group_id must be provided when clear_group_wide is True.")
            log_msg = f"Clearing history group-wide for group_id={group_id}"
            condition = MessageHistory.group_id == group_id
        else:
            if user_id is None:
                raise ValueError("user_id must be provided when clear_group_wide is False.")
            log_msg = f"Clearing history for user_id={user_id}"
            if group_id is not None:
                log_msg += f" in group_id={group_id}"
                condition = and_(MessageHistory.user_id == user_id, MessageHistory.group_id == group_id)
            else:
                log_msg += " (private messages only)"
                condition = and_(MessageHistory.user_id == user_id, MessageHistory.group_id.is_(None))

        ids_to_delete = [] # Initialize for accurate count return
        delete_stmt = delete(MessageHistory) # Base delete statement

        if limit is not None:
            if not isinstance(limit, int) or limit <= 0:
                raise ValueError("Limit must be a positive integer.")
            log_msg += f" (limit {limit})"
            select_stmt = (
                select(MessageHistory.id)
                .where(condition)
                .order_by(MessageHistory.timestamp.desc(), MessageHistory.id.desc())
                .limit(limit)
            )
            try:
                result_ids = await self.session.scalars(select_stmt)
                ids_to_delete = result_ids.all()

                if not ids_to_delete:
                    logger.info(f"No messages found matching criteria for limited deletion: {log_msg}")
                    return 0

                delete_stmt = delete_stmt.where(MessageHistory.id.in_(ids_to_delete))
                log_msg += f" - targeting {len(ids_to_delete)} specific message IDs."

            except SQLAlchemyError as e:
                logger.error(f"Database error selecting IDs for limited deletion: {e} ({log_msg})", exc_info=True)
                raise
        else:
            # Delete all matching messages without limit
            delete_stmt = delete_stmt.where(condition)

        logger.info(log_msg)
        try:
            result = await self.session.execute(delete_stmt)
            deleted_count = result.rowcount
            actual_deleted = len(ids_to_delete) if limit is not None else deleted_count
            logger.info(f"Cleared {actual_deleted} messages matching condition. (Reported rowcount: {deleted_count})")
            # No commit here
            return actual_deleted
        except SQLAlchemyError as e:
            logger.error(f"Database error executing delete statement: {e} ({log_msg})", exc_info=True)
            raise

    async def get_message(self, message_id: int) -> Optional[MessageHistory]:
        logger.debug(f"Getting message by id={message_id}")
        try:
            message = await self.session.get(MessageHistory, message_id)
            if message: logger.debug(f"Message found for id={message_id}")
            else: logger.debug(f"Message not found for id={message_id}")
            return message
        except SQLAlchemyError as e:
            logger.error(f"Error getting message by id={message_id}: {e}", exc_info=True)
            raise

    async def get_user_private_messages_as_contents(self, user_id: int, limit: int = 500) -> List[types.Content]:
        """Получает историю сообщений пользователя в формате для Gemini."""
        logger.debug(f"Getting last {limit} private messages for user_id={user_id}")
        contents: List[types.Content] = []
        try:
            stmt = (select(MessageHistory).where(and_(MessageHistory.user_id == user_id, MessageHistory.group_id.is_(None)))
                    .options(selectinload(MessageHistory.user))
                    .order_by(MessageHistory.timestamp.desc()).limit(limit))
            result = await self.session.execute(stmt)
            messages: List[MessageHistory] = list(reversed(result.scalars().all()))
            logger.debug(f"Retrieved {len(messages)} private messages for user_id={user_id} to build contents")
            
            for message in messages:
                content = self._format_message_to_content(message, is_group=False)
                if content:
                    contents.append(content)
            return contents
        except SQLAlchemyError as e:
            logger.error(f"Error getting private message history for user_id={user_id}: {e}", exc_info=True)
            return []

    async def get_group_messages(self, group_id: int, limit: int = 500) -> List[MessageHistory]:
        """Получает историю сообщений группы."""
        logger.debug(f"Getting last {limit} messages for group_id={group_id}")
        try:
            stmt = (select(MessageHistory)
                    .where(MessageHistory.group_id == group_id)
                    .options(selectinload(MessageHistory.user))
                    .order_by(MessageHistory.timestamp.desc())
                    .limit(limit))
            result = await self.session.execute(stmt)
            messages = list(reversed(result.scalars().all()))
            logger.debug(f"Retrieved {len(messages)} messages for group_id={group_id}")
            return messages
        except SQLAlchemyError as e:
            logger.error(f"Error getting group message history for group_id={group_id}: {e}", exc_info=True)
            return []

    async def get_group_messages_as_contents(self, group_id: int, limit: int = 500) -> List[types.Content]:
        """Получает историю сообщений группы в формате для Gemini."""
        logger.debug(f"Getting last {limit} messages for group_id={group_id}")
        contents: List[types.Content] = []
        try:
            messages = await self.get_group_messages(group_id=group_id, limit=limit)
            for message in messages:
                content = self._format_message_to_content(message, is_group=True)
                if content:
                    contents.append(content)
            return contents
        except Exception as e:
            logger.error(f"Error getting group message history for group_id={group_id}: {e}", exc_info=True)
            return []

    def _format_message_to_content(self, message: MessageHistory, is_group: bool = False) -> Optional[types.Content]:
        """Форматирует сообщение из БД в формат, понятный Gemini API."""
        if not message:
            logger.warning("Attempted to format None message")
            return None

        if not message.role:
            logger.warning(f"Message {message.id} has no role")
            return None

        try:
            role_str = message.role.value
            logger.debug(f"Processing message {message.id} with role {role_str}")
        except (AttributeError, ValueError) as e:
            logger.error(f"Invalid role value for message {message.id}: {e}")
            return None

        parts = []
        
        # Add text if present
        if message.text:
            try:
                # Get user's display name
                display_name = ""
                if message.user:
                    if message.user.first_name:
                        display_name = message.user.first_name
                        if message.user.last_name:
                            display_name += f" {message.user.last_name}"
                    elif message.user.username:
                        display_name = message.user.username
                    else:
                        display_name = f"User {message.user.telegram_id}"

                # Format timestamp with Ukrainian timezone
                timezone_kiev = pytz.timezone('Europe/Kiev')
                timestamp = message.timestamp.astimezone(timezone_kiev)
                time_str = timestamp.strftime("%H:%M")

                # Only add username and timestamp prefix for user messages, not bot responses
                if role_str == "user":
                    formatted_text = f"Message Id: {message.telegram_message_id} [{time_str}] {display_name}: {message.text}"
                else:
                    formatted_text = message.text

                parts.append(types.Part.from_text(text=formatted_text))
                logger.debug(f"Added text part to message {message.id}")
            except Exception as e:
                logger.error(f"Error creating text part for message {message.id}: {e}")
                return None

        # Add audio if present
        if message.audio_data:
            try:
                parts.append(types.Part.from_bytes(data=message.audio_data, mime_type="audio/ogg"))
                logger.debug(f"Added audio part to message {message.id}")
            except Exception as e:
                logger.error(f"Error creating audio part for message {message.id}: {e}")
                return None

        # Add image if present
        if message.image_data:
            try:
                # Telegram always converts images to JPEG
                parts.append(types.Part.from_bytes(data=message.image_data, mime_type="image/jpeg"))
                logger.debug(f"Added image part to message {message.id}")
            except Exception as e:
                logger.error(f"Error creating image part for message {message.id}: {e}")
                return None

        # Add video if present
        if message.video_data:
            try:
                parts.append(types.Part.from_bytes(data=message.video_data, mime_type="video/mp4"))
                logger.debug(f"Added video part to message {message.id}")
            except Exception as e:
                logger.error(f"Error creating video part for message {message.id}: {e}")
                return None

        if not parts:
            logger.warning(f"Message id={message.id} (user_id={message.user_id}, group_id={message.group_id}) has no content parts, skipping.")
            return None

        try:
            content = types.Content(role=role_str, parts=parts)
            logger.debug(f"Successfully created Content for message {message.id} with {len(parts)} parts")
            return content
        except Exception as e:
            logger.error(f"Unexpected error creating Content for message {message.id}: {e}")
            return None

class MessageDAO:
    """Асинхронный DAO для работы с моделью MessageHistory."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_message_by_internal_id(self, message_id: int) -> Optional[MessageHistory]:
        try:
            stmt = select(MessageHistory).where(MessageHistory.id == message_id)
            result = await self.session.execute(stmt)
            return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            logger.critical(f"Error getting message by internal id={message_id}: {e}", exc_info=True)
            raise

    async def get_message_by_telegram_id(self, telegram_message_id: int) -> Optional[MessageHistory]:
        try:
            stmt = select(MessageHistory).where(MessageHistory.telegram_message_id == telegram_message_id)
            result = await self.session.execute(stmt)
            return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            logger.critical(f"Error getting message by telegram_message_id={telegram_message_id}: {e}", exc_info=True)
            raise

    async def create_message(self, telegram_message_id: int, user_id: int, group_id: int, content: str | None = None) -> MessageHistory:
        values_to_insert = {
            "telegram_message_id": telegram_message_id,
            "user_id": user_id,
            "group_id": group_id,
            "text": content,
            "role": MessageRole.USER
        }

        insert_stmt = pg_insert(MessageHistory).values(**values_to_insert).returning(MessageHistory)

        try:
            result = await self.session.execute(insert_stmt)
            return result.scalar_one()
        except SQLAlchemyError as e:
            logger.critical(f"Database error during create_message for telegram_message_id={telegram_message_id}: {e}", exc_info=True)
            raise

    async def update_message_content(self, message_id: int, content: str) -> bool:
        stmt = update(MessageHistory).where(MessageHistory.id == message_id).values(text=content)
        try:
            result = await self.session.execute(stmt)
            if result.rowcount > 0:
                return True
            else:
                message_exists = await self.session.get(MessageHistory, message_id)
                return message_exists is not None
        except SQLAlchemyError as e:
            logger.critical(f"Database error updating content for message_id={message_id}: {e}", exc_info=True)
            raise