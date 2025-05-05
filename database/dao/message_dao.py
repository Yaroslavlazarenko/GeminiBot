# services/database/message_dao.py
import logging
import re
from typing import Optional, List
from datetime import datetime, timezone
import pytz # Импорт pytz может быть полезен для работы с часовыми поясами, хотя в коде используется timezone.utc
import json # Импорт для работы с метаданными, если они в формате JSON

from sqlalchemy import select, delete, and_, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from google.genai import types # Предполагается, что библиотека google-generativeai установлена
from sqlalchemy.dialects.postgresql import insert as pg_insert # Используется для ON CONFLICT, если нужно

from ..models import MessageHistory, MessageRole, User, Sticker # Убедитесь, что User и Sticker импортированы, если они используются в selectinload

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
        image_data: bytes | None = None,
        video_data: bytes | None = None,
        voice_data: bytes | None = None,
        document_data: bytes | None = None,
        group_id: int | None = None,
        telegram_message_id: int | None = None,
        message_metadata: str | None = None,
        sticker_id: int | None = None
    ) -> MessageHistory:
        """
        Добавляет сообщение в историю.

        Args:
            user_id: ID пользователя.
            role: Роль сообщения (например, MessageRole.USER, MessageRole.MODEL).
            text: Текст сообщения (опционально).
            image_data: Байты изображения (опционально).
            video_data: Байты видео (опционально).
            voice_data: Байты голосового сообщения (опционально).
            document_data: Байты документа (опционально).
            group_id: ID группы, если сообщение в группе (опционально, None для приватных).
            telegram_message_id: ID сообщения в Telegram (опционально, может использоваться для поиска/ответов).
            message_metadata: JSON-строка или другая строка с дополнительными метаданными.
            sticker_id: ID стикера (опционально).

        Returns:
            Созданный объект MessageHistory.

        Raises:
            SQLAlchemyError: При ошибке базы данных.
        """
        try:
            new_message = MessageHistory(
                user_id=user_id,
                group_id=group_id,
                role=role,
                text=text,
                image_data=image_data,
                video_data=video_data,
                voice_data=voice_data,
                document_data=document_data,
                telegram_message_id=telegram_message_id,
                message_metadata=message_metadata,
                sticker_id=sticker_id,
                timestamp=datetime.now(timezone.utc)
            )
            self.session.add(new_message)
            # Commit/flush is expected to be handled by the caller/session context
            logger.debug(f"Added new message for user_id={user_id}, group_id={group_id}")
            return new_message
        except SQLAlchemyError as e:
            logger.error(f"Database error adding message for user_id={user_id}, group_id={group_id}: {e}", exc_info=True)
            raise

    async def clear_history(
        self,
        *, # Force keyword arguments for clarity
        user_id: int | None = None,
        group_id: int | None = None,
        clear_group_wide: bool = False,
        limit: int | None = None
    ) -> int:
        """
        Очищает историю сообщений по заданным критериям.
        Может очищать приватную историю пользователя, историю пользователя в группе,
        или всю историю группы.

        Args:
            user_id: ID пользователя (обязателен, если clear_group_wide=False).
            group_id: ID группы (обязателен, если clear_group_wide=True; опционален для user_id).
            clear_group_wide: Если True, очищает всю историю группы (требует group_id).
                              Если False, очищает историю, связанную с user_id
                              (либо приватную, либо в указанной группе).
            limit: Если указан, удаляет только последние N сообщений, соответствующих критериям.

        Returns:
            Количество удаленных сообщений.

        Raises:
            ValueError: При некорректных аргументах (например, отсутствует group_id при clear_group_wide=True).
            SQLAlchemyError: При ошибке базы данных.
        """
        condition = None
        log_msg_base = "Clearing history"

        if clear_group_wide:
            if group_id is None:
                raise ValueError("group_id must be provided when clear_group_wide is True.")
            condition = MessageHistory.group_id == group_id
            log_msg = f"{log_msg_base} group-wide for group_id={group_id}"
        else:
            if user_id is None:
                raise ValueError("user_id must be provided when clear_group_wide is False.")
            if group_id is not None:
                condition = and_(MessageHistory.user_id == user_id, MessageHistory.group_id == group_id)
                log_msg = f"{log_msg_base} for user_id={user_id} in group_id={group_id}"
            else:
                condition = and_(MessageHistory.user_id == user_id, MessageHistory.group_id.is_(None))
                log_msg = f"{log_msg_base} for user_id={user_id} (private messages only)"

        ids_to_delete: list[int] = []
        deleted_count = 0

        if limit is not None:
            if not isinstance(limit, int) or limit <= 0:
                raise ValueError("Limit must be a positive integer.")
            log_msg += f" (limit {limit})"

            # Для удаления последних N сообщений, сначала выбираем их ID по убыванию timestamp
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

                delete_stmt = delete(MessageHistory).where(MessageHistory.id.in_(ids_to_delete))
                log_msg += f" - targeting {len(ids_to_delete)} specific message IDs."

            except SQLAlchemyError as e:
                logger.error(f"Database error selecting IDs for limited deletion: {e} ({log_msg})", exc_info=True)
                raise
        else:
            # Если лимит не указан, удаляем все сообщения по условию
            delete_stmt = delete(MessageHistory).where(condition)

        logger.info(log_msg)
        try:
            result = await self.session.execute(delete_stmt)
            # rowcount не всегда точен для DELETE с WHERE IN или без лимита в async SQLAlchemy
            # Если был лимит, ids_to_delete.count() - точное число таргетированных
            # Если лимита не было, rowcount - лучшее приближение
            actual_deleted = len(ids_to_delete) if limit is not None else result.rowcount
            logger.info(f"Executed delete. Reported rowcount: {result.rowcount}. Actual targeted (if limit): {actual_deleted}")
            # Commit/flush is expected to be handled by the caller/session context
            return actual_deleted
        except SQLAlchemyError as e:
            logger.error(f"Database error executing delete statement: {e} ({log_msg})", exc_info=True)
            raise

    async def get_message(self, message_id: int) -> Optional[MessageHistory]:
        """
        Получает сообщение по его внутреннему ID из базы данных.

        Args:
            message_id: Внутренний ID сообщения.

        Returns:
            Объект MessageHistory или None, если не найден.

        Raises:
            SQLAlchemyError: При ошибке базы данных.
        """
        logger.debug(f"Getting message by id={message_id}")
        try:
            # Используем session.get для получения по первичному ключу, это эффективно.
            # Eager loading здесь не нужно, если только сразу после получения
            # сообщения не планируется доступ к связанным объектам user/sticker.
            # Если нужно, добавьте .options(selectinload(...)).
            message = await self.session.get(MessageHistory, message_id)
            if message:
                logger.debug(f"Message found for id={message_id}")
            else:
                logger.debug(f"Message not found for id={message_id}")
            return message
        except SQLAlchemyError as e:
            logger.error(f"Database error getting message by id={message_id}: {e}", exc_info=True)
            raise

    async def get_user_private_messages_as_contents(self, user_id: int, limit: int = 500) -> List[types.Content]:
        """
        Получает последние 'limit' приватных сообщений пользователя
        и возвращает их в хронологическом порядке (от старых к новым) в формате для Gemini.

        Args:
            user_id: ID пользователя.
            limit: Максимальное количество последних сообщений для получения.

        Returns:
            Список объектов google.genai.types.Content.

        Raises:
            SQLAlchemyError: При ошибке базы данных.
        """
        logger.debug(f"Getting last {limit} private messages for user_id={user_id} for Gemini contents")
        contents: List[types.Content] = []
        try:
            # Получаем последние 'limit' сообщений в обратном хронологическом порядке (новые сверху)
            stmt = (select(MessageHistory)
                    .where(and_(MessageHistory.user_id == user_id, MessageHistory.group_id.is_(None)))
                    .options(
                        selectinload(MessageHistory.user), # Загружаем связанного пользователя
                        selectinload(MessageHistory.sticker) # Загружаем связанный стикер
                    )
                    # Сортируем по убыванию timestamp, чтобы получить самые новые первыми
                    .order_by(MessageHistory.timestamp.desc(), MessageHistory.id.desc())
                    .limit(limit))

            result = await self.session.execute(stmt)
            messages: List[MessageHistory] = result.scalars().all()

            # Разворачиваем список в Python, чтобы получить хронологический порядок (от старых к новым)
            messages.reverse()

            logger.debug(f"Retrieved and ordered {len(messages)} private messages for user_id={user_id} to build contents")

            for message in messages:
                content = self._format_message_to_content(message, is_group=False)
                if content:
                    contents.append(content)

            logger.debug(f"Generated {len(contents)} Gemini contents for user_id={user_id}")
            return contents
        except SQLAlchemyError as e:
            logger.error(f"Database error getting private message history for user_id={user_id}: {e}", exc_info=True)
            raise # Пробрасываем ошибку базы данных
        except Exception as e:
             # Логируем любые другие ошибки, которые могут возникнуть при форматировании
             logger.error(f"Error processing private message history for user_id={user_id} into contents: {e}", exc_info=True)
             # Не пробрасываем, возвращаем пустой список, так как это ошибка обработки, не базы
             return []


    async def get_group_messages(self, group_id: int, limit: int = 500) -> List[MessageHistory]:
        """
        Получает последние 'limit' сообщений группы
        и возвращает их в хронологическом порядке (от старых к новым).

        Args:
            group_id: ID группы.
            limit: Максимальное количество последних сообщений для получения.

        Returns:
            Список объектов MessageHistory.

        Raises:
            SQLAlchemyError: При ошибке базы данных.
        """
        logger.debug(f"Getting last {limit} messages for group_id={group_id}")
        try:
            # Получаем последние 'limit' сообщений в обратном хронологическом порядке (новые сверху)
            stmt = (select(MessageHistory)
                    .where(MessageHistory.group_id == group_id)
                    .options(
                        selectinload(MessageHistory.user), # Загружаем связанного пользователя
                        selectinload(MessageHistory.sticker) # Загружаем связанный стикер
                    )
                    # Сортируем по убыванию timestamp, чтобы получить самые новые первыми
                    .order_by(MessageHistory.timestamp.desc(), MessageHistory.id.desc())
                    .limit(limit))

            result = await self.session.execute(stmt)
            messages = result.scalars().all()

            # Разворачиваем список в Python, чтобы получить хронологический порядок (от старых к новым)
            messages.reverse()

            logger.debug(f"Retrieved and ordered {len(messages)} messages for group_id={group_id}")
            return messages
        except SQLAlchemyError as e:
            logger.error(f"Database error getting group message history for group_id={group_id}: {e}", exc_info=True)
            raise # Пробрасываем ошибку базы данных

    async def get_group_messages_as_contents(self, group_id: int, limit: int = 500) -> List[types.Content]:
        """
        Получает последние 'limit' сообщений группы
        и возвращает их в хронологическом порядке (от старых к новым) в формате для Gemini.
        Использует get_group_messages и форматирует результат.

        Args:
            group_id: ID группы.
            limit: Максимальное количество последних сообщений для получения.

        Returns:
            Список объектов google.genai.types.Content.
        """
        logger.debug(f"Getting last {limit} messages for group_id={group_id} as contents")
        contents: List[types.Content] = []
        try:
            # get_group_messages уже возвращает последние N сообщений в хронологическом порядке
            messages = await self.get_group_messages(group_id=group_id, limit=limit)

            for message in messages:
                content = self._format_message_to_content(message, is_group=True)
                if content:
                    contents.append(content)

            logger.debug(f"Generated {len(contents)} Gemini contents for group_id={group_id}")
            return contents
        except Exception as e:
            # Логируем любые ошибки, которые могут возникнуть при форматировании,
            # ошибки БД уже залогированы в get_group_messages
            logger.error(f"Error processing group message history for group_id={group_id} into contents: {e}", exc_info=True)
            return [] # Возвращаем пустой список в случае ошибки форматирования

    def _format_message_to_content(self, message: MessageHistory, is_group: bool = False) -> Optional[types.Content]:
        """
        Форматирует объект MessageHistory из БД в формат, понятный Gemini API (types.Content).
        Включает метаданные (время, ID в Telegram, информация о пользователе в группе)
        и контент (текст, медиа).

        Метаданные добавляются как первая текстовая часть, затем основной текст,
        затем медиа-части.

        Args:
            message: Объект MessageHistory из базы данных.
            is_group: Является ли сообщение частью группового чата.

        Returns:
            Объект types.Content или None, если форматирование невозможно.
        """
        if not message or not message.role:
            logger.warning(f"Cannot format invalid message: {message}. Message ID: {message.id if message else 'N/A'}")
            return None

        # 1. Определение роли для API Gemini
        try:
            role_str = None
            if isinstance(message.role, MessageRole):
                # Преобразуем enum в строковое значение
                enum_value = message.role.value
            elif isinstance(message.role, str):
                 # Если в БД строка, пытаемся использовать ее напрямую
                 enum_value = message.role
            else:
                logger.error(f"Unexpected type for message role {type(message.role)} for message {message.id}. Cannot format.")
                return None

            # Маппинг ролей на роли Gemini API
            # API ожидает 'user' или 'model'
            if enum_value == MessageRole.USER.value or enum_value == "user":
                 role_str = "user"
            elif enum_value == MessageRole.MODEL.value or enum_value == "model":
                 role_str = "model"
            else:
                 # Пропускаем сообщения с неизвестными ролями для API
                 logger.warning(f"Unsupported role '{enum_value}' for Gemini API in message {message.id}. Skipping.")
                 return None

            logger.debug(f"Processing message {message.id} with mapped role '{role_str}' (original: {message.role})")

        except Exception as e:
            logger.error(f"Error determining role for message {message.id}: {e}", exc_info=True)
            return None

        parts = []

        # 2. Формирование метаданных сообщения
        metadata_parts_list = []

        # Добавляем исходные метаданные из колонки, если есть
        if message.message_metadata:
            # Пытаемся парсить как JSON для более читаемого вывода, если возможно
            try:
                metadata_obj = json.loads(message.message_metadata)
                # Форматируем JSON для читаемости или добавляем как есть
                metadata_parts_list.append(f"Metadata: {json.dumps(metadata_obj, indent=2)}")
            except json.JSONDecodeError:
                # Если не JSON, добавляем как обычную строку
                 metadata_parts_list.append(f"Metadata: {message.message_metadata}")
            except Exception as e:
                 logger.warning(f"Error processing message_metadata for message {message.id}: {e}")
                 metadata_parts_list.append(f"Metadata: {message.message_metadata}")


        # Добавляем другие важные поля как метаданные
        if message.telegram_message_id:
            metadata_parts_list.append(f"Telegram Message ID: {message.telegram_message_id}")

        # Добавляем информацию о времени (всегда в UTC)
        if message.timestamp:
            timestamp_utc = message.timestamp
            if timestamp_utc.tzinfo is None:
                 # Предполагаем, что наивные (naive) метки времени в БД - это UTC
                 timestamp_utc = timestamp_utc.replace(tzinfo=timezone.utc)
            else:
                 # Если есть часовой пояс, конвертируем в UTC
                 timestamp_utc = timestamp_utc.astimezone(timezone.utc)

            formatted_time = timestamp_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
            metadata_parts_list.append(f"Time: {formatted_time}")

        # Добавляем информацию о пользователе в групповых чатах
        if is_group:
            user_info = f"User ID: {message.user_id}"
            if message.user:
                # Construct full name from first_name and last_name
                full_name = message.user.first_name or ""
                if message.user.last_name:
                    full_name = f"{full_name} {message.user.last_name}".strip()
                if full_name:
                    user_info = f"User: {full_name} (ID: {message.user_id})"
            metadata_parts_list.append(user_info)

        # Объединяем все метаданные в одну строку, если они есть
        message_metadata_str = "\n".join(metadata_parts_list) if metadata_parts_list else None

        # 3. Добавление частей контента (метаданные, текст, медиа)
        # Метаданные добавляем как первую текстовую часть
        if message_metadata_str:
            try:
                parts.append(types.Part.from_text(text=f"--- Message Info ---\n{message_metadata_str}\n--------------------"))
                logger.debug(f"Added metadata text part to message {message.id}")
            except Exception as e:
                logger.error(f"Error creating metadata text part for message {message.id}: {e}")
                # Не прерываем, просто логируем ошибку создания части

        # Добавляем основной текст сообщения
        if message.text:
            try:
                parts.append(types.Part.from_text(text=message.text))
                logger.debug(f"Added main text part to message {message.id}")
            except Exception as e:
                logger.error(f"Error creating main text part for message {message.id}: {e}")
                # Не прерываем

        # Добавляем стикер (если есть и загружен)
        if message.sticker and message.sticker.image_data:
            try:
                # MIME тип для стикера Telegram (обычно WebP)
                parts.append(types.Part.from_bytes(data=message.sticker.image_data, mime_type="image/webp"))
                logger.debug(f"Added sticker part to message {message.id}")
            except Exception as e:
                logger.error(f"Error creating sticker part for message {message.id}: {e}", exc_info=True)

        # Добавляем голосовое сообщение (если есть)
        if message.voice_data:
            try:
                # MIME тип для OGG audio
                parts.append(types.Part.from_bytes(data=message.voice_data, mime_type="audio/ogg"))
                logger.debug(f"Added audio part to message {message.id}")
            except Exception as e:
                logger.error(f"Error creating audio part for message {message.id}: {e}")

        # Добавляем изображение (если есть)
        if message.image_data:
            try:
                # MIME тип для JPEG изображения
                parts.append(types.Part.from_bytes(data=message.image_data, mime_type="image/jpeg"))
                logger.debug(f"Added image part to message {message.id}")
            except Exception as e:
                logger.error(f"Error creating image part for message {message.id}: {e}")

        # Добавляем видео (если есть)
        if message.video_data:
            try:
                # MIME тип для MP4 видео
                parts.append(types.Part.from_bytes(data=message.video_data, mime_type="video/mp4"))
                logger.debug(f"Added video part to message {message.id}")
            except Exception as e:
                logger.error(f"Error creating video part for message {message.id}: {e}")
                
        # Добавляем документ (если есть)
        if message.document_data:
            try:
                # Определяем MIME тип документа на основе метаданных
                mime_type = "application/octet-stream"  # По умолчанию
                
                # Пытаемся найти MIME тип в метаданных
                if message.message_metadata and "MIME type:" in message.message_metadata:
                    # Простой парсинг строки метаданных
                    mime_match = re.search(r"MIME type:\s*([\w\-\.]+\/[\w\-\.]+)", message.message_metadata)
                    if mime_match:
                        mime_type = mime_match.group(1)
                
                # Определяем, является ли документ изображением
                is_image = mime_type.startswith("image/")
                
                # Если это изображение, добавляем его как изображение
                if is_image:
                    parts.append(types.Part.from_bytes(data=message.document_data, mime_type=mime_type))
                    logger.debug(f"Added document as image part (mime: {mime_type}) to message {message.id}")
                else:
                    # Для неизображений добавляем только метаданные в текст
                    logger.debug(f"Document is not an image (mime: {mime_type}), not adding binary data for message {message.id}")
                    # Метаданные уже добавлены выше
            except Exception as e:
                logger.error(f"Error creating document part for message {message.id}: {e}", exc_info=True)

        # Проверяем, были ли добавлены хоть какие-то части контента (кроме метаданных, если они одни)
        # Если добавлена только метаданная часть, но нет основного текста или медиа, возможно, это не полное сообщение.
        # Решаем, включать ли сообщения только с метаданными. Для чат-истории, возможно, нет.
        # Учитываем, что метаданные добавляются как текстовая часть.
        # Проверим, есть ли что-то *кроме* первой части, если она только метаданные.
        has_main_content = False
        if len(parts) > 1:
             has_main_content = True
        elif len(parts) == 1 and (message.text or message.image_data or message.video_data or message.voice_data or message.sticker_id):
             # Если есть только одна часть, но при этом в исходном сообщении был текст или медиа,
             # значит, единственная часть - это либо текст, либо медиа (если метаданных не было или они не добавились),
             # либо это метаданные + что-то еще, что не было добавлено из-за ошибки форматирования.
             # Это условие немного сложное. Проще проверить, есть ли у исходного сообщения что-то, что должно было стать частью.
             if message.text or message.image_data or message.video_data or message.voice_data or message.sticker_id:
                 has_main_content = True
             # Если parts=[metadata_part] и у сообщения не было ни текста, ни медиа - пропускаем.
             # Если parts=[] - пропускаем.

        # Если нет ни текста, ни медиа, ни успешно добавленных частей (кроме возможной метаданной части), пропускаем
        if not parts or (len(parts) == 1 and message_metadata_str and not has_main_content):
            logger.warning(f"Message id={message.id} (user_id={message.user_id}, group_id={message.group_id}) has no substantial content parts (text/media/sticker) or parts creation failed, skipping.")
            return None


        # 4. Создание объекта Content
        try:
            content = types.Content(role=role_str, parts=parts)
            logger.debug(f"Successfully created Content for message {message.id} with {len(parts)} parts")
            return content
        except Exception as e:
            logger.error(f"Unexpected error creating types.Content object for message {message.id} (role='{role_str}', parts count={len(parts)}): {e}", exc_info=True)
            return None


# Вторая DAO для операций с одиночными сообщениями
class MessageDAO:
    """Асинхронный DAO для работы с моделью MessageHistory для операций с одиночными сообщениями."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_message_by_internal_id(self, message_id: int) -> Optional[MessageHistory]:
        """
        Получает сообщение по его внутреннему ID, включая связанные объекты.

        Args:
            message_id: Внутренний ID сообщения.

        Returns:
            Объект MessageHistory с загруженными user и sticker, или None.

        Raises:
            SQLAlchemyError: При ошибке базы данных.
        """
        logger.debug(f"MessageDAO: Getting message by internal id={message_id}")
        try:
            # Используем select с eager loading для получения связанных объектов сразу
            stmt = (select(MessageHistory)
                    .where(MessageHistory.id == message_id)
                    .options(
                        selectinload(MessageHistory.user),
                        selectinload(MessageHistory.sticker)
                    ))
            result = await self.session.execute(stmt)
            message = result.scalar_one_or_none()
            if message:
                logger.debug(f"MessageDAO: Message found for internal id={message_id}")
            else:
                logger.debug(f"MessageDAO: Message not found for internal id={message_id}")
            return message
        except SQLAlchemyError as e:
            logger.critical(f"MessageDAO: Error getting message by internal id={message_id}: {e}", exc_info=True)
            raise

    async def get_message_by_telegram_id(self, telegram_message_id: int) -> Optional[MessageHistory]:
        """
        Получает сообщение по его Telegram ID, включая связанные объекты.
        Предполагается, что telegram_message_id уникален или вам нужен первый найденный.
        Если telegram_message_id может повторяться (например, в разных чатах), нужно добавить group_id или user_id.

        Args:
            telegram_message_id: ID сообщения в Telegram.

        Returns:
            Объект MessageHistory с загруженными user и sticker, или None.

        Raises:
            SQLAlchemyError: При ошибке базы данных.
        """
        logger.debug(f"MessageDAO: Getting message by telegram_message_id={telegram_message_id}")
        try:
            # Используем select с eager loading
            stmt = (select(MessageHistory)
                    .where(MessageHistory.telegram_message_id == telegram_message_id)
                     .options(
                        selectinload(MessageHistory.user),
                        selectinload(MessageHistory.sticker)
                    ))
            result = await self.session.execute(stmt)
            message = result.scalar_one_or_none() # Получаем одно или None
            if message:
                logger.debug(f"MessageDAO: Message found for telegram_message_id={telegram_message_id}")
            else:
                logger.debug(f"MessageDAO: Message not found for telegram_message_id={telegram_message_id}")
            return message
        except SQLAlchemyError as e:
            logger.critical(f"MessageDAO: Error getting message by telegram_message_id={telegram_message_id}: {e}", exc_info=True)
            raise

    async def create_message(
        self,
        telegram_message_id: int,
        user_id: int,
        group_id: int | None,
        role: MessageRole,
        text: str | None = None,
        image_data: bytes | None = None,
        video_data: bytes | None = None,
        voice_data: bytes | None = None,
        document_data: bytes | None = None,
        message_metadata: str | None = None,
        sticker_id: int | None = None
    ) -> MessageHistory:
        """
        Создает новое сообщение в базе данных.

        Args:
             telegram_message_id: ID сообщения в Telegram.
             user_id: ID пользователя.
             group_id: ID группы, если сообщение в группе (None для приватных).
             role: Роль сообщения (USER, MODEL и т.д.).
             text: Текст сообщения (опционально).
             image_data: Байты изображения (опционально).
             video_data: Байты видео (опционально).
             voice_data: Байты голосового сообщения (опционально).
             document_data: Байты документа (опционально).
             message_metadata: Дополнительные метаданные (опционально).
             sticker_id: ID стикера (опционально).

        Returns:
            Созданный объект MessageHistory.

        Raises:
            SQLAlchemyError: При ошибке базы данных.
        """
        # Note: This method is very similar to MessageHistoryDAO.add_message.
        # Consider if both DAOs are needed or if one can serve both purposes.
        # For now, implementing as requested based on the second DAO.

        values_to_insert = {
            "telegram_message_id": telegram_message_id,
            "user_id": user_id,
            "group_id": group_id,
            "role": role, # Используем переданную роль
            "text": text,
            "image_data": image_data,
            "video_data": video_data,
            "voice_data": voice_data,
            "document_data": document_data,
            "message_metadata": message_metadata,
            "sticker_id": sticker_id,
            "timestamp": datetime.now(timezone.utc) # Добавляем timestamp
        }

        # Используем pg_insert, который позволяет RETURNING.
        # Если telegram_message_id + group_id уникален, можно добавить .on_conflict_do_update()
        # или .on_conflict_do_nothing(), но в данном случае просто INSERT.
        insert_stmt = pg_insert(MessageHistory).values(**values_to_insert).returning(MessageHistory)

        logger.debug(f"MessageDAO: Creating message with telegram_message_id={telegram_message_id}, user_id={user_id}, group_id={group_id}, role={role}")

        try:
            result = await self.session.execute(insert_stmt)
            # Commit/flush is expected to be handled by the caller/session context
            created_message = result.scalar_one()
            logger.debug(f"MessageDAO: Successfully created message with ID {created_message.id}")
            return created_message
        except SQLAlchemyError as e:
            logger.critical(f"MessageDAO: Database error during create_message for telegram_message_id={telegram_message_id}: {e}", exc_info=True)
            raise

    async def update_message_content(self, message_id: int, text: str | None = None, image_data: bytes | None = None, video_data: bytes | None = None, voice_data: bytes | None = None, document_data: bytes | None = None, message_metadata: str | None = None, sticker_id: int | None = None) -> bool:
        """
        Обновляет контент сообщения по его внутреннему ID.

        Args:
            message_id: Внутренний ID сообщения для обновления.
            text: Новый текст (опционально).
            image_data: Новые байты изображения (опционально).
            video_data: Новые байты видео (опционально).
            voice_data: Новые байты голосового сообщения (опционально).
            document_data: Новые байты документа (опционально).
            message_metadata: Новые метаданные (опционально).
            sticker_id: Новый ID стикера (опционально).
            # Примечание: Обновление timestamp и role обычно нежелательно через этот метод.

        Returns:
            True, если сообщение было найдено и обновлено; False, если сообщение не найдено.

        Raises:
            SQLAlchemyError: При ошибке базы данных.
        """
        update_values = {}
        if text is not None: update_values['text'] = text
        if image_data is not None: update_values['image_data'] = image_data
        if video_data is not None: update_values['video_data'] = video_data
        if voice_data is not None: update_values['voice_data'] = voice_data
        if document_data is not None: update_values['document_data'] = document_data
        if message_metadata is not None: update_values['message_metadata'] = message_metadata
        if sticker_id is not None: update_values['sticker_id'] = sticker_id # Может быть None, если нужно убрать стикер

        if not update_values:
             logger.debug(f"MessageDAO: No update values provided for message_id={message_id}. Doing nothing.")
             # Можно проверить существование сообщения здесь, если нужно
             # return (await self.session.get(MessageHistory, message_id)) is not None
             return False # Нет данных для обновления -> считаем не обновленным

        stmt = update(MessageHistory).where(MessageHistory.id == message_id).values(**update_values)

        logger.debug(f"MessageDAO: Attempting to update message_id={message_id} with values: {list(update_values.keys())}")

        try:
            result = await self.session.execute(stmt)
            # Commit/flush is expected to be handled by the caller/session context
            updated_count = result.rowcount
            logger.debug(f"MessageDAO: Update executed for message_id={message_id}. Rowcount: {updated_count}")
            return updated_count > 0
        except SQLAlchemyError as e:
            logger.critical(f"MessageDAO: Database error updating content for message_id={message_id}: {e}", exc_info=True)
            raise