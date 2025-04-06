# services/database/dao.py
import logging
from typing import Optional, List
from datetime import datetime, timezone

from sqlalchemy import select, delete, update, and_, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError, NoResultFound
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .models import MessageHistory, User, Group, MessageRole
from google.genai import types

logger = logging.getLogger(__name__)

class AsyncDAO:
    """Асинхронный Data Access Object для работы с базой данных."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # --- User Methods ---
    # ... (get_or_create_user, get_user_by_telegram_id, update_user_settings remain unchanged) ...
    async def get_or_create_user(self, telegram_id: int, username: str | None = None, first_name: str | None = None, last_name: str | None = None, **kwargs) -> User:
        logger.debug(f"Attempting to get or create/update user for telegram_id={telegram_id}")
        # Handle None username gracefully for insert/update
        values_to_insert = {
            "telegram_id": telegram_id,
            "username": username if username is not None else str(telegram_id), # Use tg_id if username is None
            "first_name": first_name,
            "last_name": last_name,
            **kwargs
        }
        values_to_update = {
            "username": username if username is not None else str(telegram_id),
            "first_name": first_name,
            "last_name": last_name,
        }
        insert_stmt = pg_insert(User).values(**values_to_insert).on_conflict_do_update(
            index_elements=['telegram_id'], set_=values_to_update
        ).returning(User)
        try:
            result = await self.session.execute(insert_stmt)
            user = result.scalar_one()
            logger.info(f"Successfully got or created/updated user: {user.id=} {user.telegram_id=}")
            return user
        except SQLAlchemyError as e:
            logger.error(f"Database error during get_or_create_user for telegram_id={telegram_id}: {e}", exc_info=True)
            await self.session.rollback() # Rollback on error
            raise

    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        logger.debug(f"Getting user by telegram_id={telegram_id}")
        try:
            stmt = select(User).where(User.telegram_id == telegram_id)
            result = await self.session.execute(stmt)
            user = result.scalar_one_or_none()
            if user: logger.debug(f"User found for telegram_id={telegram_id}, user_id={user.id}")
            else: logger.debug(f"User not found for telegram_id={telegram_id}")
            return user
        except SQLAlchemyError as e:
            logger.error(f"Error getting user by telegram_id={telegram_id}: {e}", exc_info=True)
            # Consider whether to raise or return None depending on caller expectation
            raise # Or return None

    async def update_user_settings(self, user_id: int, responds_to_text: bool | None = None, responds_to_voice: bool | None = None, transcribe_voice_only: bool | None = None) -> bool:
        logger.debug(f"Updating settings for user_id={user_id}")
        values_to_update = {}
        if responds_to_text is not None: values_to_update["responds_to_text"] = responds_to_text
        if responds_to_voice is not None: values_to_update["responds_to_voice"] = responds_to_voice
        if transcribe_voice_only is not None: values_to_update["transcribe_voice_only"] = transcribe_voice_only
        if not values_to_update:
            logger.warning(f"No settings provided to update for user_id={user_id}")
            return False
        stmt = update(User).where(User.id == user_id).values(**values_to_update)
        try:
            result = await self.session.execute(stmt)
            if result.rowcount > 0:
                logger.info(f"Successfully updated settings for user_id={user_id}")
                return True
            else:
                # This is not necessarily an error, the user might not exist (though unlikely if called after get_or_create)
                logger.warning(f"User with id={user_id} not found for settings update (or settings already had the target value).")
                # Check if user exists before returning False definitively
                user_exists = await self.session.get(User, user_id)
                return user_exists is not None # Return True if user exists but settings weren't changed
        except SQLAlchemyError as e:
            logger.error(f"Database error updating settings for user_id={user_id}: {e}", exc_info=True)
            # Don't rollback here, let the middleware handle it
            raise # Re-raise the exception


    # --- Group Methods ---
    # ... (get_group_by_internal_id, get_group_by_telegram_id, get_or_create_group remain unchanged) ...
    async def get_group_by_internal_id(self, group_id: int) -> Optional[Group]:
        """Получает группу по ее ВНУТРЕННЕМУ ID базы данных."""
        logger.debug(f"Getting group by internal DB id={group_id}")
        try:
            # Use select().where() for consistency and potential future options loading
            stmt = select(Group).where(Group.id == group_id)
            result = await self.session.execute(stmt)
            group = result.scalar_one_or_none()
            # group = await self.session.get(Group, group_id) # .get() works fine too for PK lookup
            if group:
                logger.debug(f"Group found by internal id: {group.id=}, {group.telegram_chat_id=}, {group.name=}")
            else:
                logger.debug(f"Group not found for internal id={group_id}")
            return group
        except SQLAlchemyError as e:
            logger.error(f"Error getting group by internal id={group_id}: {e}", exc_info=True)
            raise # Or return None

    async def get_group_by_telegram_id(self, telegram_chat_id: int) -> Optional[Group]:
        """Получает группу по ее УНИКАЛЬНОМУ telegram_chat_id."""
        logger.debug(f"Getting group by telegram_chat_id={telegram_chat_id}")
        try:
            stmt = select(Group).where(Group.telegram_chat_id == telegram_chat_id)
            result = await self.session.execute(stmt)
            group = result.scalar_one_or_none()
            if group:
                logger.debug(f"Group found for telegram_chat_id={telegram_chat_id}: {group.id=}, {group.name=}")
            else:
                 logger.debug(f"Group not found for telegram_chat_id={telegram_chat_id}")
            return group
        except SQLAlchemyError as e: # Catch NoResultFound specifically if needed, though scalar_one_or_none handles it
            logger.error(f"Error getting group by telegram_chat_id={telegram_chat_id}: {e}", exc_info=True)
            raise # Or return None

    async def get_or_create_group(
        self,
        telegram_chat_id: int,
        name: str # Имя группы (например, из Telegram)
    ) -> Group:
        """
        Получает группу по telegram_chat_id или создает ее.
        Если группа существует, обновляет ее имя на переданное `name`.
        Использует INSERT ... ON CONFLICT DO UPDATE для атомарности.
        Новые группы будут созданы с настройками по умолчанию (responds=True).
        Существующие группы НЕ будут иметь свои настройки responds изменены здесь.
        """
        logger.debug(f"Attempting to get or create/update group for telegram_chat_id={telegram_chat_id}")
        values_to_insert = {
            "telegram_chat_id": telegram_chat_id,
            "name": name
            # responds_to_text/voice defaults are handled by the DB schema (server_default)
        }
        values_to_update = {
            "name": name,
            # НЕ обновляем responds_to_text/voice здесь, это делается отдельной командой
        }

        insert_stmt = pg_insert(Group).values(
            **values_to_insert
        ).on_conflict_do_update(
            index_elements=['telegram_chat_id'], # Уникальный индекс
            set_=values_to_update # Обновляем только имя при конфликте
        ).returning(Group) # Возвращаем всю строку группы

        try:
            result = await self.session.execute(insert_stmt)
            group = result.scalar_one() # Should always return one row due to INSERT or UPDATE
            logger.info(f"Successfully got or created/updated group: {group.id=} {group.telegram_chat_id=} {group.name=}")
            return group
        except SQLAlchemyError as e:
            logger.error(f"Database error during get_or_create_group for telegram_chat_id={telegram_chat_id}: {e}", exc_info=True)
            # Let middleware handle rollback
            raise # Передаем исключение выше

    # --- NEW Group Settings Method ---
    async def update_group_settings(self, group_id: int, responds_to_text: bool | None = None, responds_to_voice: bool | None = None) -> bool:
        """
        Обновляет настройки ответа для конкретной группы (по ее внутреннему ID).
        """
        logger.debug(f"Updating settings for group_id={group_id}")
        values_to_update = {}
        if responds_to_text is not None: values_to_update["responds_to_text"] = responds_to_text
        if responds_to_voice is not None: values_to_update["responds_to_voice"] = responds_to_voice

        if not values_to_update:
            logger.warning(f"No settings provided to update for group_id={group_id}")
            return False # Nothing to update

        stmt = update(Group).where(Group.id == group_id).values(**values_to_update)
        try:
            result = await self.session.execute(stmt)
            if result.rowcount > 0:
                logger.info(f"Successfully updated settings for group_id={group_id}")
                return True
            else:
                # Group not found or settings already had the target value
                logger.warning(f"Group with internal id={group_id} not found for settings update (or settings unchanged).")
                # Check if group exists
                group_exists = await self.session.get(Group, group_id)
                return group_exists is not None # True if group exists but settings didn't change
        except SQLAlchemyError as e:
            logger.error(f"Database error updating settings for group_id={group_id}: {e}", exc_info=True)
            raise # Let middleware handle rollback/commit


    # --- Message History Methods ---
    # ... (add_message, clear_history, get_message, get_user_private_messages_as_contents,
    #      get_group_messages_as_contents, _format_message_to_content remain unchanged) ...
    async def add_message(self, user_id: int, role: MessageRole, text: str | None = None, audio_data: bytes | None = None, image_data: bytes | None = None, video_data: bytes | None = None, group_id: int | None = None ) -> MessageHistory:
        # group_id здесь - это ВНУТРЕННИЙ id из таблицы groups
        log_msg = f"Adding message for user_id={user_id}, role={role.value}"
        if group_id: log_msg += f", group_id={group_id}" # Используем внутренний ID
        logger.debug(log_msg)
        new_message = MessageHistory(user_id=user_id, group_id=group_id, role=role, text=text, audio_data=audio_data, image_data=image_data, video_data=video_data, timestamp=datetime.now(timezone.utc))
        self.session.add(new_message)
        # Don't flush here, let commit handle it or flush explicitly if ID is needed immediately
        logger.debug(f"Message for user_id={user_id} added to session.")
        return new_message # Return the object, ID might be populated after commit/flush

    async def clear_history(
        self,
        *, # Force keyword arguments for clarity
        user_id: int | None = None,
        group_id: int | None = None,
        clear_group_wide: bool = False,
        limit: int | None = None
    ) -> int:
        """
        Clears message history based on provided criteria.

        Args:
            user_id: The internal database ID of the user whose messages to clear.
                     Required if clear_group_wide is False.
            group_id: The internal database ID of the group context.
                      If None and clear_group_wide is False, clears user's private messages.
                      Required if clear_group_wide is True.
            clear_group_wide: If True, clears all messages in the specified group_id,
                              ignoring user_id. Requires group_id.
            limit: If provided, clears only the most recent 'limit' messages matching
                   the criteria.

        Returns:
            The number of messages deleted.

        Raises:
            ValueError: If arguments are inconsistent (e.g., clear_group_wide without group_id).
            SQLAlchemyError: If a database error occurs.
        """
        if clear_group_wide:
            if group_id is None:
                raise ValueError("group_id must be provided when clear_group_wide is True.")
            log_msg = f"Clearing history group-wide for group_id={group_id}"
            condition = MessageHistory.group_id == group_id
            # user_id is ignored when clearing group-wide
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

        if limit is not None:
            if not isinstance(limit, int) or limit <= 0:
                raise ValueError("Limit must be a positive integer.")
            log_msg += f" (limit {limit})"
            # To delete with limit, we need to select the IDs first, then delete by ID
            # ORDER BY timestamp DESC (or id DESC if timestamp is not reliably ordered)
            select_stmt = (
                select(MessageHistory.id)
                .where(condition)
                .order_by(MessageHistory.timestamp.desc(), MessageHistory.id.desc()) # Order by timestamp then ID
                .limit(limit)
            )
            try:
                result_ids = await self.session.scalars(select_stmt)
                ids_to_delete = result_ids.all()

                if not ids_to_delete:
                    logger.info(f"No messages found matching criteria for deletion: {log_msg}")
                    return 0

                # Now construct the delete statement based on the selected IDs
                delete_stmt = delete(MessageHistory).where(MessageHistory.id.in_(ids_to_delete))
                log_msg += f" - targeting {len(ids_to_delete)} specific message IDs."

            except SQLAlchemyError as e:
                logger.error(f"Database error selecting IDs for limited deletion: {e} ({log_msg})", exc_info=True)
                raise
        else:
            # Delete all matching messages without limit
            delete_stmt = delete(MessageHistory).where(condition)

        logger.info(log_msg)
        try:
            result = await self.session.execute(delete_stmt)
            # Note: result.rowcount might not be perfectly accurate with some DB backends/drivers
            # when using "IN" clause with many IDs, but it's usually the best available metric.
            # If using the IDs_to_delete approach, len(ids_to_delete) is the accurate count
            # *before* the delete operation. result.rowcount is the count *after*.
            deleted_count = result.rowcount
            actual_deleted = len(ids_to_delete) if limit is not None else deleted_count # More accurate count for limited deletes
            logger.info(f"Cleared {actual_deleted} messages matching condition. (Reported rowcount: {deleted_count})")
            return actual_deleted # Return the count based on selected IDs if limit was used
        except SQLAlchemyError as e:
            logger.error(f"Database error executing delete statement: {e} ({log_msg})", exc_info=True)
            raise

    async def get_message(self, message_id: int) -> Optional[MessageHistory]:
        logger.debug(f"Getting message by id={message_id}")
        try:
            # Prefer session.get for primary key lookups
            message = await self.session.get(MessageHistory, message_id)
            # stmt = select(MessageHistory).where(MessageHistory.id == message_id)
            # result = await self.session.execute(stmt)
            # message = result.scalar_one_or_none()
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
            # Use reversed() for potentially better memory usage than creating a new list
            messages: List[MessageHistory] = list(reversed(result.scalars().all()))
            # messages.reverse() # This modifies the list in place, also fine
            logger.debug(f"Retrieved {len(messages)} private messages for user_id={user_id} to build contents")
            for message in messages:
                content = self._format_message_to_content(message, is_group=False)
                if content: contents.append(content)
            return contents
        except SQLAlchemyError as e:
            logger.error(f"Error getting private message history for user_id={user_id}: {e}", exc_info=True)
            return [] # Return empty list on error

    async def get_group_messages_as_contents(self, group_id: int, limit: int = 500) -> List[types.Content]:
        # group_id здесь - это ВНУТРЕННИЙ id из таблицы groups
        logger.debug(f"Getting last {limit} messages for group_id={group_id}") # Используем внутренний ID
        contents: List[types.Content] = []
        try:
            # Optional check if group exists (can be removed if get_or_create guarantees existence before call)
            # group_exists = await self.get_group_by_internal_id(group_id) # Already checked in handler usually
            # if not group_exists:
            #     logger.warning(f"Attempted to get messages for non-existent internal group_id={group_id}")
            #     return []

            stmt = (select(MessageHistory).where(MessageHistory.group_id == group_id) # Используем внутренний ID
                    .options(selectinload(MessageHistory.user)) # Eager load user data
                    .order_by(MessageHistory.timestamp.desc()).limit(limit))
            result = await self.session.execute(stmt)
            messages: List[MessageHistory] = list(reversed(result.scalars().all()))
            logger.debug(f"Retrieved {len(messages)} messages for group_id={group_id} to build contents") # Используем внутренний ID
            for message in messages:
                content = self._format_message_to_content(message, is_group=True)
                if content: contents.append(content)
            return contents
        except SQLAlchemyError as e:
            logger.error(f"Error getting group message history for group_id={group_id}: {e}", exc_info=True) # Используем внутренний ID
            return []

    # --- Helper Methods ---
    def _format_message_to_content(self, message: MessageHistory, is_group: bool = False) -> Optional[types.Content]:
        parts = []
        message_text = message.text
        # Add prefix ONLY if it's a group message AND it's from a user
        if is_group and message.role == MessageRole.USER:
            # User should have been loaded via selectinload in get_group_messages_as_contents
            if message.user:
                # Use first_name or fallback to a generic User ID string
                user_display_name = message.user.first_name or f"User_{message.user.telegram_id}"
                prefix = f"{user_display_name}: "
                # Prepend prefix only if there is text
                if message_text:
                     message_text = f"{prefix}{message_text}"
                # else: # If only media, don't add prefix to nothing
                #     pass
            else:
                # This case should be less likely with selectinload, but handle defensively
                logger.warning(f"User data not loaded for message_id={message.id} in group_id={message.group_id}. Cannot add prefix.")
                if message_text:
                    message_text = f"Unknown User: {message_text}"

        # Add parts based on content
        if message_text: parts.append(types.Part.from_text(text=message_text))
        # TODO: Determine actual MIME types if possible, or use reasonable defaults
        if message.audio_data: parts.append(types.Part.from_bytes(data=message.audio_data, mime_type="audio/ogg"))
        if message.image_data: parts.append(types.Part.from_bytes(data=message.image_data, mime_type="image/jpeg"))
        if message.video_data: parts.append(types.Part.from_bytes(data=message.video_data, mime_type="video/mp4"))

        if parts:
            # Ensure role is a valid string value from the enum
            role_str = message.role.value
            try:
                return types.Content(role=role_str, parts=parts)
            except ValueError as e:
                 logger.error(f"Invalid role value '{role_str}' for message {message.id}: {e}")
                 return None # Skip message with invalid role
        else:
            logger.warning(f"Message id={message.id} (user_id={message.user_id}, group_id={message.group_id}) has no content parts, skipping.")
            return None