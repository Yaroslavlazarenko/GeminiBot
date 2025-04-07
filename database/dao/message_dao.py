# services/database/message_dao.py
import logging
from typing import Optional, List
from datetime import datetime, timezone

from sqlalchemy import select, delete, and_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from google.genai import types

from ..models import MessageHistory, MessageRole

logger = logging.getLogger(__name__)

class MessageHistoryDAO:
    """Асинхронный DAO для работы с моделью MessageHistory."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_message(self, user_id: int, role: MessageRole, text: str | None = None, audio_data: bytes | None = None, image_data: bytes | None = None, video_data: bytes | None = None, group_id: int | None = None ) -> MessageHistory:
        log_msg = f"Adding message for user_id={user_id}, role={role.value}"
        if group_id: log_msg += f", group_id={group_id}"
        logger.debug(log_msg)
        new_message = MessageHistory(user_id=user_id, group_id=group_id, role=role, text=text, audio_data=audio_data, image_data=image_data, video_data=video_data, timestamp=datetime.now(timezone.utc))
        self.session.add(new_message)
        # Don't flush or commit here, let the session manager handle it.
        # await self.session.flush([new_message]) # Flush only if you NEED the ID immediately
        logger.debug(f"Message for user_id={user_id} added to session.")
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
        logger.debug(f"Getting last {limit} private messages for user_id={user_id}")
        contents: List[types.Content] = []
        try:
            stmt = (select(MessageHistory).where(and_(MessageHistory.user_id == user_id, MessageHistory.group_id.is_(None)))
                    .order_by(MessageHistory.timestamp.desc()).limit(limit))
            result = await self.session.execute(stmt)
            messages: List[MessageHistory] = list(reversed(result.scalars().all()))
            logger.debug(f"Retrieved {len(messages)} private messages for user_id={user_id} to build contents")
            for message in messages:
                content = self._format_message_to_content(message, is_group=False)
                if content: contents.append(content)
            return contents
        except SQLAlchemyError as e:
            logger.error(f"Error getting private message history for user_id={user_id}: {e}", exc_info=True)
            return []

    async def get_group_messages_as_contents(self, group_id: int, limit: int = 500) -> List[types.Content]:
        logger.debug(f"Getting last {limit} messages for group_id={group_id}")
        contents: List[types.Content] = []
        try:
            stmt = (select(MessageHistory).where(MessageHistory.group_id == group_id)
                    .options(selectinload(MessageHistory.user)) # Eager load user data
                    .order_by(MessageHistory.timestamp.desc()).limit(limit))
            result = await self.session.execute(stmt)
            messages: List[MessageHistory] = list(reversed(result.scalars().all()))
            logger.debug(f"Retrieved {len(messages)} messages for group_id={group_id} to build contents")
            for message in messages:
                content = self._format_message_to_content(message, is_group=True)
                if content: contents.append(content)
            return contents
        except SQLAlchemyError as e:
            logger.error(f"Error getting group message history for group_id={group_id}: {e}", exc_info=True)
            return []

    def _format_message_to_content(self, message: MessageHistory, is_group: bool = False) -> Optional[types.Content]:
        parts = []
        message_text = message.text
        if is_group and message.role == MessageRole.USER:
            if message.user:
                user_display_name = message.user.first_name or f"User_{message.user.telegram_id}"
                prefix = f"{user_display_name}: "
                if message_text:
                     message_text = f"{prefix}{message_text}"
                # else: pass # Don't add prefix if only media
            else:
                logger.warning(f"User data not loaded for message_id={message.id} in group_id={message.group_id}. Cannot add prefix.")
                if message_text:
                    message_text = f"Unknown User: {message_text}"

        # Add parts based on content
        if message_text: parts.append(types.Part.from_text(text=message_text))
        if message.audio_data: parts.append(types.Part.from_bytes(data=message.audio_data, mime_type="audio/ogg"))
        if message.image_data: parts.append(types.Part.from_bytes(data=message.image_data, mime_type="image/jpeg"))
        if message.video_data: parts.append(types.Part.from_bytes(data=message.video_data, mime_type="video/mp4"))

        if parts:
            role_str = message.role.value
            try:
                return types.Content(role=role_str, parts=parts)
            except ValueError as e:
                 logger.error(f"Invalid role value '{role_str}' for message {message.id}: {e}")
                 return None
        else:
            logger.warning(f"Message id={message.id} (user_id={message.user_id}, group_id={message.group_id}) has no content parts, skipping.")
            return None