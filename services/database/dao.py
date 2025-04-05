# services/database/dao.py
import logging
from typing import Optional, List
from datetime import datetime, timezone

from sqlalchemy import select, delete, update, and_, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError, NoResultFound # Убрали MultipleResultsFound, т.к. telegram_chat_id уникален
from sqlalchemy.dialects.postgresql import insert as pg_insert

# Импортируем модели и Enum из вашего файла models.py
# Убедитесь, что путь импорта правильный
from .models import MessageHistory, User, Group, MessageRole
from google.genai import types # Используется для форматирования вывода

logger = logging.getLogger(__name__)

class AsyncDAO:
    """Асинхронный Data Access Object для работы с базой данных."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # --- User Methods ---
    # ... (методы get_or_create_user, get_user_by_telegram_id, update_user_settings остаются без изменений) ...
    async def get_or_create_user(self, telegram_id: int, username: str | None = None, first_name: str | None = None, last_name: str | None = None, **kwargs) -> User:
        logger.debug(f"Attempting to get or create/update user for telegram_id={telegram_id}")
        values_to_insert = {"telegram_id": telegram_id, "username": username, "first_name": first_name, "last_name": last_name, **kwargs}
        values_to_update = {"username": username, "first_name": first_name, "last_name": last_name} # Обновляем имя при конфликте
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
            await self.session.rollback()
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
            raise

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
                logger.warning(f"User with id={user_id} not found for settings update.")
                return False
        except SQLAlchemyError as e:
            logger.error(f"Database error updating settings for user_id={user_id}: {e}", exc_info=True)
            raise

    # --- Group Methods (Обновленные) ---

    async def get_group_by_internal_id(self, group_id: int) -> Optional[Group]:
        """Получает группу по ее ВНУТРЕННЕМУ ID базы данных."""
        logger.debug(f"Getting group by internal DB id={group_id}")
        try:
            group = await self.session.get(Group, group_id) # .get() работает с Primary Key
            if group:
                logger.debug(f"Group found by internal id: {group.id=}, {group.telegram_chat_id=}, {group.name=}")
            else:
                logger.debug(f"Group not found for internal id={group_id}")
            return group
        except SQLAlchemyError as e:
            logger.error(f"Error getting group by internal id={group_id}: {e}", exc_info=True)
            raise

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
        except SQLAlchemyError as e:
            logger.error(f"Error getting group by telegram_chat_id={telegram_chat_id}: {e}", exc_info=True)
            raise

    async def get_or_create_group(
        self,
        telegram_chat_id: int,
        name: str # Имя группы (например, из Telegram)
    ) -> Group:
        """
        Получает группу по telegram_chat_id или создает ее.
        Если группа существует, обновляет ее имя на переданное `name`.
        Использует INSERT ... ON CONFLICT DO UPDATE для атомарности.
        """
        logger.debug(f"Attempting to get or create/update group for telegram_chat_id={telegram_chat_id}")
        values_to_update = {
            "name": name,
        }

        insert_stmt = pg_insert(Group).values(
            # Передаем значения как явные keyword arguments
            telegram_chat_id=telegram_chat_id,
            name=name
        ).on_conflict_do_update(
            index_elements=['telegram_chat_id'], # Уникальный индекс
            set_=values_to_update # Обновляем имя при конфликте
        ).returning(Group) # Возвращаем всю строку группы

        try:
            result = await self.session.execute(insert_stmt)
            group = result.scalar_one()
            logger.info(f"Successfully got or created/updated group: {group.id=} {group.telegram_chat_id=} {group.name=}")
            return group
        except SQLAlchemyError as e:
            # Логгирование ошибки здесь остается полезным
            logger.error(f"Database error during get_or_create_group for telegram_chat_id={telegram_chat_id}: {e}", exc_info=True)
            raise # Передаем исключение выше, чтобы middleware его поймал


    # --- Message History Methods ---
    # Методы add_message, clear_history, get_message,
    # get_user_private_messages_as_contents, get_group_messages_as_contents, _format_message_to_content
    # остаются ТАКИМИ ЖЕ, как в предыдущем ответе.
    # Важно: они принимают/используют ВНУТРЕННИЙ group.id, а не telegram_chat_id.
    # Вы сначала получаете объект Group через get_or_create_group(telegram_chat_id, name),
    # а затем используете group.id в этих методах.

    async def add_message(self, user_id: int, role: MessageRole, text: str | None = None, audio_data: bytes | None = None, image_data: bytes | None = None, video_data: bytes | None = None, group_id: int | None = None ) -> MessageHistory:
        # group_id здесь - это ВНУТРЕННИЙ id из таблицы groups
        log_msg = f"Adding message for user_id={user_id}, role={role.value}"
        if group_id: log_msg += f", group_id={group_id}" # Используем внутренний ID
        logger.debug(log_msg)
        new_message = MessageHistory(user_id=user_id, group_id=group_id, role=role, text=text, audio_data=audio_data, image_data=image_data, video_data=video_data, timestamp=datetime.now(timezone.utc))
        self.session.add(new_message)
        logger.debug(f"Message for user_id={user_id} added to session.")
        return new_message

    async def clear_history(self, user_id: int, group_id: int | None = None) -> int:
        # group_id здесь - это ВНУТРЕННИЙ id из таблицы groups
        log_msg = f"Clearing message history for user_id={user_id}"
        if group_id is not None:
            log_msg += f" in group_id={group_id}" # Используем внутренний ID
            condition = and_(MessageHistory.user_id == user_id, MessageHistory.group_id == group_id)
        else:
            log_msg += " (private messages only)"
            condition = and_(MessageHistory.user_id == user_id, MessageHistory.group_id.is_(None))
        logger.info(log_msg)
        stmt = delete(MessageHistory).where(condition)
        try:
            result = await self.session.execute(stmt)
            deleted_count = result.rowcount
            logger.info(f"Cleared {deleted_count} messages matching condition.")
            return deleted_count
        except SQLAlchemyError as e:
            logger.error(f"Database error clearing history for user_id={user_id} (group_id={group_id}): {e}", exc_info=True)
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

    async def get_user_private_messages_as_contents(self, user_id: int, limit: int = 50) -> List[types.Content]:
        logger.debug(f"Getting last {limit} private messages for user_id={user_id}")
        contents: List[types.Content] = []
        try:
            stmt = (select(MessageHistory).where(and_(MessageHistory.user_id == user_id, MessageHistory.group_id.is_(None)))
                    .order_by(MessageHistory.timestamp.desc()).limit(limit))
            result = await self.session.execute(stmt)
            messages: List[MessageHistory] = list(result.scalars().all())
            messages.reverse()
            logger.debug(f"Retrieved {len(messages)} private messages for user_id={user_id} to build contents")
            for message in messages:
                content = self._format_message_to_content(message, is_group=False)
                if content: contents.append(content)
            return contents
        except SQLAlchemyError as e:
            logger.error(f"Error getting private message history for user_id={user_id}: {e}", exc_info=True)
            return []

    async def get_group_messages_as_contents(self, group_id: int, limit: int = 50) -> List[types.Content]:
        # group_id здесь - это ВНУТРЕННИЙ id из таблицы groups
        logger.debug(f"Getting last {limit} messages for group_id={group_id}") # Используем внутренний ID
        contents: List[types.Content] = []
        try:
            # Проверяем, существует ли группа с таким внутренним ID (опционально, но полезно)
            # group_exists = await self.get_group_by_internal_id(group_id)
            # if not group_exists:
            #     logger.warning(f"Attempted to get messages for non-existent internal group_id={group_id}")
            #     return []

            stmt = (select(MessageHistory).where(MessageHistory.group_id == group_id) # Используем внутренний ID
                    .options(selectinload(MessageHistory.user))
                    .order_by(MessageHistory.timestamp.desc()).limit(limit))
            result = await self.session.execute(stmt)
            messages: List[MessageHistory] = list(result.scalars().all())
            messages.reverse()
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
        if is_group and message.role == MessageRole.USER and message_text:
            if message.user:
                prefix = f"{message.user.first_name or f'User_{message.user.telegram_id}'}: "
                message_text = f"{prefix}{message_text}"
            else:
                logger.warning(f"User data not loaded for message_id={message.id} in group_id={message.group_id}. Cannot add prefix.")
                message_text = f"Unknown User: {message_text}"

        if message_text: parts.append(types.Part.from_text(text=message_text))
        if message.audio_data: parts.append(types.Part.from_bytes(data=message.audio_data, mime_type="audio/ogg")) # TODO: Correct mime type
        if message.image_data: parts.append(types.Part.from_bytes(data=message.image_data, mime_type="image/jpeg")) # TODO: Correct mime type
        if message.video_data: parts.append(types.Part.from_bytes(data=message.video_data, mime_type="video/mp4")) # TODO: Correct mime type

        if parts:
            role_str = message.role.value
            return types.Content(role=role_str, parts=parts)
        else:
            logger.warning(f"Message id={message.id} (user_id={message.user_id}, group_id={message.group_id}) has no content parts, skipping.")
            return None