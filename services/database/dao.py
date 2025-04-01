# services/database/dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from services.database.models import MessageHistory, User, MessageRole # Импортируем Enum
from typing import Optional, List
from datetime import datetime, timezone
# Убираем datetime, т.к. timestamp теперь устанавливается БД
from sqlalchemy import select, delete
from google.genai import types
import logging # Используем logging

logger = logging.getLogger(__name__)

class DAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    # --- WRITE METHODS ---

    async def create_user(self, username: str, telegram_id: int, first_name: str | None = None, last_name: str | None = None) -> User:
        """Создает экземпляр пользователя и добавляет его в сессию."""
        # Ошибки SQLAlchemy будут обработаны выше (в middleware)
        logger.info(f"Creating user object for telegram_id={telegram_id}")
        new_user = User(
            username=username,
            telegram_id=telegram_id,
            first_name=first_name,
            last_name=last_name
            # Поля настроек получат значения по умолчанию из модели
        )
        self.session.add(new_user)
        await self.session.flush([new_user]) # Получаем начальное состояние (но без ID до коммита)
        logger.info(f"User object for telegram_id={telegram_id} added to session.")
        return new_user

    async def add_message(self, user_id: int, role: MessageRole, text: str | None = None, audio_data: bytes | None = None, image_data: bytes | None = None, video_data: bytes | None = None) -> MessageHistory:
        """Добавляет экземпляр сообщения в сессию."""
        # Ошибки SQLAlchemy будут обработаны выше (в middleware)
        logger.debug(f"Adding message for user_id={user_id}, role={role.value}")
        new_message = MessageHistory(
            user_id=user_id,
            role=role.value, # Передаем Enum напрямую
            text=text,
            audio_data=audio_data,
            image_data=image_data,
            video_data=video_data,
            # --- ВОЗВРАЩАЕМ УСТАНОВКУ TIMESTAMP В КОДЕ ---
            # Используем UTC, так как поле в БД DateTime(timezone=True)
            timestamp=datetime.now(timezone.utc)
            # -----------------------------------------
        )
        self.session.add(new_message)
        await self.session.flush([new_message]) # Опционально
        logger.debug(f"Message for user_id={user_id} added to session.")
        return new_message

    async def clear_history(self, user_id: int) -> int:
        """Удаляет историю сообщений для пользователя. Возвращает кол-во удаленных строк."""
        # Ошибки SQLAlchemy будут обработаны выше (в middleware)
        logger.info(f"Clearing message history for user_id={user_id}")
        stmt = delete(MessageHistory).where(MessageHistory.user_id == user_id)
        result = await self.session.execute(stmt)
        # await self.session.flush() # Не обязательно после delete, т.к. commit все равно будет
        deleted_count = result.rowcount
        logger.info(f"Cleared {deleted_count} messages for user_id={user_id}")
        return deleted_count

    # --- READ METHODS ---

    async def get_message(self, message_id: int) -> Optional[MessageHistory]:
        """Получает сообщение по ID."""
        try:
            # get удобен для поиска по PK
            result = await self.session.get(MessageHistory, message_id)
            return result
        except SQLAlchemyError as e:
            logger.error(f"Error getting message by id={message_id}: {e}", exc_info=True)
            return None # Возвращаем None при ошибке чтения

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
            return None # Возвращаем None при ошибке чтения

    async def get_user_messages_as_contents(self, user_id: int, limit: int = 50) -> List[types.Content]:
        """
        Получает последние сообщения пользователя и форматирует их для Gemini API.
        Обрабатывает несколько частей (parts) в одном сообщении.
        """
        contents: List[types.Content] = []
        try:
            stmt = (
                select(MessageHistory)
                .where(MessageHistory.user_id == user_id)
                .order_by(MessageHistory.timestamp.desc())
                .limit(limit)
            )
            result = await self.session.execute(stmt)
            messages: List[MessageHistory] = list(result.scalars().all())
            messages.reverse()

            logger.debug(f"Retrieved last {len(messages)} messages for user_id={user_id} to build contents")

            for message in messages:
                parts = [] # Создаем список частей для КАЖДОГО сообщения из БД

                # Сначала текстовая часть, если есть
                if message.text:
                    parts.append(types.Part.from_text(text=message.text))

                # Затем бинарные данные (можно иметь несколько в одном сообщении)
                if message.audio_data:
                    # TODO: Определить MIME-тип динамически или из модели
                    parts.append(types.Part.from_bytes(data=message.audio_data, mime_type="audio/ogg"))
                if message.image_data:
                    # TODO: Определить MIME-тип
                    parts.append(types.Part.from_bytes(data=message.image_data, mime_type="image/jpeg")) # Пример
                if message.video_data:
                    # TODO: Определить MIME-тип
                    parts.append(types.Part.from_bytes(data=message.video_data, mime_type="video/mp4")) # Пример

                # Только если у сообщения были какие-то части, добавляем его в историю
                if parts:
                    # message.role уже строка ('user' или 'model')
                    contents.append(types.Content(role=message.role, parts=parts))
                else:
                    logger.warning(f"Message id={message.id} for user_id={user_id} has no content parts, skipping.")

            return contents

        except SQLAlchemyError as e:
            logger.error(f"Error getting message history for user_id={user_id}: {e}", exc_info=True)
            return []