# telegram/handlers/messages/photo.py

import logging
import asyncio
import os
import io
from typing import Dict, Set, List, Optional, Tuple, Any

from aiogram import F, Router
from aiogram.types import Message, PhotoSize
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai.gemini_client import get_image_response
from database.models import User, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
from ..utils import send_error_message, get_group_or_none, handle_gemini_result
from ..message_batcher import message_batcher

logger = logging.getLogger(__name__)
router = Router()

# --- Управление Медиагруппами ---

# Кэш для медиагрупп: ключ - media_group_id, значение - список (сообщение, байты фото)
media_group_cache: Dict[str, List[Tuple[Message, bytes]]] = {}
# Таймеры для обработки медиагрупп
media_group_timers: Dict[str, asyncio.Task] = {}
# Время ожидания последнего фото в медиагруппе (в секундах)
MEDIA_GROUP_TIMEOUT = 5.0 # Можно настроить (например, 2.0 или 3.0)

# --- Вспомогательные функции ---

async def get_best_photo_data(message: Message) -> Optional[bytes]:
    """Получает данные фото наилучшего качества."""
    if not message.photo:
        logger.warning(f"No photos found in message {message.message_id}")
        return None

    # Пытаемся взять последнее фото (обычно самое большое)
    photo: Optional[PhotoSize] = message.photo[-1] if message.photo else None
    if not photo:
         logger.warning(f"Photo list is empty in message {message.message_id}")
         return None

    try:
        logger.debug(f"Attempting to download photo file_id={photo.file_id} (size {photo.width}x{photo.height}, {photo.file_size} bytes)")
        file = await message.bot.get_file(photo.file_id)
        if not file.file_path:
            logger.error(f"File path is missing for photo file_id={photo.file_id}")
            return None

        downloaded_file = await message.bot.download_file(file.file_path)
        if downloaded_file is None:
            logger.error(f"Failed to download photo from path={file.file_path}, received None")
            return None

        photo_data = downloaded_file.read()
        logger.debug(f"Downloaded {len(photo_data)} bytes for photo file_id={photo.file_id}")
        return photo_data

    except (TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError) as e:
        # Логгируем специфичные ошибки Telegram API
        if "file is too big" in str(e).lower():
             logger.warning(f"Telegram API error downloading photo file_id={photo.file_id}: File is too big ({photo.file_size} bytes). Error: {e}")
        else:
             logger.error(f"Telegram API error downloading photo file_id={photo.file_id}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error downloading photo file_id={photo.file_id}: {e}", exc_info=True)
        return None


async def process_media_group(
    media_group_id: str,
    chat_id: int,
    user_telegram_id: int, # Получаем telegram_id
    bot,
    session_factory: async_sessionmaker[AsyncSession], # Получаем фабрику сессий
) -> None:
    """
    Обрабатывает собранную медиагруппу целиком, используя НОВУЮ сессию БД.
    Эта функция вызывается как фоновая задача ПОСЛЕ того, как все фото группы
    были получены (или истек таймаут).

    ПОЧЕМУ НУЖНА НОВАЯ СЕССИЯ (session_factory)?
    1. Исходные сессии закрыты: Сессии SQLAlchemy, созданные middleware для каждого
       отдельного сообщения с фото (`photo_handler`), уже закрыты к моменту вызова
       этой фоновой задачи. Использовать DAO из тех сессий невозможно.
    2. Атомарность операции: Вся обработка медиагруппы (сохранение всех фото,
       инфо-сообщения, получение истории, сохранение ответа AI) должна быть
       единой транзакцией. Если что-то пойдет не так, вся операция должна
       откатиться. Новая сессия обеспечивает эту атомарность.
    3. Независимость: Обработка одной медиагруппы не должна влиять на другие
       одновременно обрабатываемые группы или сообщения. Отдельная сессия
       гарантирует изоляцию.
    """
    photos = media_group_cache.get(media_group_id, [])
    # Очищаем кэш и таймер *сразу после получения данных*, чтобы избежать гонок состояний
    if media_group_id in media_group_cache:
        del media_group_cache[media_group_id]
        logger.debug(f"Cleared media group cache for {media_group_id} upon processing start.")
    if media_group_id in media_group_timers:
        del media_group_timers[media_group_id]
        logger.debug(f"Cleared media group timer for {media_group_id} upon processing start.")

    if not photos:
        logger.warning(f"No photos found for media_group_id={media_group_id} when processing started.")
        return

    first_message = photos[0][0] # Для ответа и получения информации о чате

    # --- СОЗДАЕМ НОВУЮ СЕССИЮ И КОНТЕКСТ ДЛЯ ЭТОЙ ЗАДАЧИ ---
    logger.info(f"Creating new session to process media group {media_group_id}...")
    async with session_factory() as session: # Создаем сессию специально для этой задачи
        # Создаем DAO с НОВОЙ сессией
        user_dao = UserDAO(session)
        group_dao = GroupDAO(session)
        message_dao = MessageHistoryDAO(session)
        logger.info(f"Created new session and DAOs for media group {media_group_id} processing.")

        try:
            # --- Получаем пользователя и группу внутри НОВОЙ сессии ---
            user = await user_dao.get_user_by_telegram_id(user_telegram_id)
            if not user:
                 # Пытаемся получить актуальную информацию о пользователе из Telegram
                 try:
                     tg_user_info = await bot.get_chat(user_telegram_id) # get_chat работает и для пользователей
                     user = await user_dao.get_or_create_user(
                         telegram_id=user_telegram_id,
                         username=tg_user_info.username or str(user_telegram_id),
                         first_name=tg_user_info.first_name,
                         last_name=tg_user_info.last_name
                     )
                     logger.info(f"User {user_telegram_id} fetched/created within process_media_group session.")
                 except Exception as tg_err:
                      logger.error(f"Failed to get Telegram user info for {user_telegram_id}: {tg_err}")
                      user = None # Не удалось получить информацию

                 if not user:
                      logger.error(f"Failed definitively to get or create user {user_telegram_id} within process_media_group session.")
                      await send_error_message(first_message, "Не вдалося обробити ваші дані користувача.")
                      return # Критическая ошибка, выходим

            chat = first_message.chat
            group = await get_group_or_none(group_dao, chat) # Используем group_dao с новой сессией
            group_db_id = group.id if group else None

            logger.info(f"Processing media group {media_group_id} with {len(photos)} photos from user {user.telegram_id} in chat {chat_id}")

            # Отправляем индикатор "typing"
            try: await bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception as e: logger.warning(f"Failed to send chat action 'typing' to {chat_id}: {e}")

            # --- Сохраняем сообщения в НОВУЮ сессию ---
            # Инфо-сообщение (одно на всю группу)
            await message_dao.add_message(
                user_id=user.id, role=MessageRole.USER,
                text=f"Message info: next message contains {len(photos)} photos in a media group, Message ID: {first_message.message_id}, Message Time: {first_message.date}",
                group_id=group_db_id,
                telegram_message_id=first_message.message_id # ID первого сообщения группы как идентификатор
            )
            # Каждое фото (со своим telegram_message_id)
            saved_photo_count = 0
            for msg, photo_data in photos:
                await message_dao.add_message(
                    user_id=user.id, role=MessageRole.USER,
                    image_data=photo_data,
                    group_id=group_db_id,
                    telegram_message_id=msg.message_id # ID конкретного фото-сообщения
                )
                saved_photo_count += 1
            logger.debug(f"Added info message and {saved_photo_count} photos from media group {media_group_id} to session.")

            # Получаем историю сообщений для контекста (из новой сессии)
            message_history = []
            try:
                if group_db_id is not None:
                    message_history = await message_dao.get_group_messages_as_contents(group_id=group_db_id)
                else:
                    message_history = await message_dao.get_user_private_messages_as_contents(user_id=user.id)
                logger.debug(f"Fetched {len(message_history)} messages for context from new session for media group {media_group_id}.")
            except Exception as db_error:
                logger.error(f"Error getting message history in new session for media group {media_group_id}: {db_error}", exc_info=True)
                # Продолжаем без истории, если не удалось получить

            if not message_history:
                logger.warning(f"Message history is empty before calling Gemini for media group {media_group_id}")

            # Вызываем AI для обработки фото
            gemini_result = await get_text_response(message_history=message_history, user=user)

            # Обрабатываем результат от AI (передаем DAO с НОВОЙ сессией)
            # handle_gemini_result сам вызовет message_dao.add_message для ответа модели
            await handle_gemini_result(
                gemini_result, first_message, # Отвечаем на первое сообщение группы
                message_dao=message_dao, # DAO с новой сессией
                user_dao=user_dao,       # DAO с новой сессией
                user=user,
                group_db_id=group_db_id
            )

            # --- COMMIT НОВОЙ СЕССИИ ---
            # Все операции выше были добавлены в НОВУЮ сессию.
            # Только после commit они будут сохранены в БД.
            await session.commit()
            logger.info(f"Successfully processed and committed media group {media_group_id}")

        except SQLAlchemyError as db_err:
             # Ловим ошибки БД внутри этой сессии
             logger.error(f"Database error during process_media_group {media_group_id} for user {user_telegram_id}: {db_err}", exc_info=True)
             await session.rollback() # Откатываем ВСЕ изменения этой сессии
             logger.warning(f"Rolled back session for process_media_group {media_group_id} due to DB error.")
             # Отправляем сообщение об ошибке пользователю
             await send_error_message(first_message, "Помилка бази даних під час збереження ваших фотографій.")
        except Exception as e:
             # Ловим другие ошибки во время обработки
             logger.error(f"Unexpected error processing media group {media_group_id} for user {user_telegram_id}: {e}", exc_info=True)
             try:
                 # Пытаемся откатить сессию, если ошибка произошла до commit
                 await session.rollback() # Откатываем ВСЕ изменения этой сессии
                 logger.warning(f"Rolled back session for process_media_group {media_group_id} due to handler error.")
             except Exception as rollback_err:
                 logger.error(f"Error during rollback after handler error for media group {media_group_id}: {rollback_err}", exc_info=True)
             # Отправляем сообщение об ошибке пользователю
             await send_error_message(first_message, "🤯 Ой! Сталася неочікувана помилка під час обробки ваших фотографій.")
        # Блок finally не нужен для rollback здесь, он есть в except


async def schedule_media_group_processing(
    media_group_id: str,
    chat_id: int,
    user_telegram_id: int, # Передаем telegram_id
    bot,
    session_factory: async_sessionmaker[AsyncSession], # Передаем фабрику сессий
) -> None:
    """Планирует или перепланирует обработку медиагруппы через таймаут."""
    # Отменяем предыдущий таймер для этой группы, если он еще не выполнен
    if media_group_id in media_group_timers:
        task = media_group_timers[media_group_id]
        if not task.done():
            task.cancel()
            logger.debug(f"Cancelled previous timer for media group {media_group_id}")

    # Создаем новый таймер, передавая фабрику сессий, чтобы process_media_group
    # мог создать свою собственную сессию БД для обработки.
    new_task = asyncio.create_task(
        process_media_group_after_timeout(
            media_group_id, chat_id, user_telegram_id, bot, session_factory
        )
    )
    media_group_timers[media_group_id] = new_task
    logger.debug(f"Scheduled/Rescheduled processing for media group {media_group_id} in {MEDIA_GROUP_TIMEOUT}s")


async def process_media_group_after_timeout(
    media_group_id: str,
    chat_id: int,
    user_telegram_id: int, # Получаем telegram_id
    bot,
    session_factory: async_sessionmaker[AsyncSession], # Получаем фабрику сессий
) -> None:
    """Ожидает таймаут и запускает обработку медиагруппы."""
    try:
        await asyncio.sleep(MEDIA_GROUP_TIMEOUT)
        logger.info(f"Timeout expired for media group {media_group_id}. Starting processing.")
        # Вызываем основную функцию обработки, передавая фабрику сессий
        await process_media_group(media_group_id, chat_id, user_telegram_id, bot, session_factory)
    except asyncio.CancelledError:
        # Таймер был отменен (например, пришло новое фото в группу) - это нормально
        logger.debug(f"Media group {media_group_id} processing timer cancelled.")
    except Exception as e:
        # Логгируем неожиданные ошибки в самой задаче ожидания/запуска
        logger.error(f"Error in delayed media group processing task for {media_group_id}: {e}", exc_info=True)
        # Попытка очистить таймер, если он еще существует
        if media_group_id in media_group_timers:
            del media_group_timers[media_group_id]


# --- Основной Хендлер ---

@router.message(F.photo)
async def photo_handler(
    message: Message,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO,
    user_dao: UserDAO,
    user: User
) -> None:
    """Обработчик фотографий"""
    chat = message.chat
    try:
        # Get group context if message is from group
        group = await get_group_or_none(group_dao, chat) if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP] else None
        group_db_id = group.id if group else None

        # Get user display name for better identification
        user_display_name = message.from_user.full_name
        if not user_display_name:
            user_display_name = f"User {user.telegram_id}"

        # Check global response setting first
        if user.is_global_disabled:
            logger.debug(f"Ignoring photo from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id} due to global USER disable.")
            return
        if group and group.is_global_disabled:
            logger.debug(f"Ignoring photo from user {user_display_name} (ID: {user.telegram_id}) in group chat {chat.id} due to global GROUP disable.")
            return

        # Check photo specific settings
        if not getattr(user, 'responds_to_photo', True):
            logger.debug(f"Ignoring photo from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id} due to USER photo setting.")
            return
        if group and not getattr(group, 'responds_to_photo', True):
            logger.debug(f"Ignoring photo from user {user_display_name} (ID: {user.telegram_id}) in group chat {chat.id} due to GROUP photo setting.")
            return

        photo_sizes = message.photo
        if not photo_sizes:
            logger.error("Message marked as photo but no photo sizes found")
            await send_error_message(message, "Помилка: некоректні дані фотографії.")
            return

        # Get the largest photo size
        photo = max(photo_sizes, key=lambda p: p.width * p.height)

        # Process photo
        try:
            # Download photo file
            file = await message.bot.get_file(photo.file_id)
            if not file.file_path:
                logger.error(f"File path is missing for photo file_id={photo.file_id}")
                await send_error_message(message, "Помилка: не вдалося отримати шлях до файлу фотографії.")
                return

            downloaded_file = await message.bot.download_file(file.file_path)
            if downloaded_file is None:
                logger.error(f"Failed to download photo from path={file.file_path}, received None")
                await send_error_message(message, "Помилка: не вдалося завантажити фотографію (отримано None).")
                return

            photo_data = downloaded_file.read()

        except Exception as e:
            logger.error(f"Error processing photo: {e}", exc_info=True)
            await send_error_message(message, "Помилка: не вдалося обробити фотографію.")
            return

        # Формируем метаданные для фотографии
        is_forwarded = bool(message.forward_from or message.forward_from_chat or message.forward_sender_name or message.forward_date)
        
        if is_forwarded:
            # This is a forwarded photo
            metadata = f"Message info: FORWARDED photo shared by {user_display_name} (User ID: {user.telegram_id})"
            
            # Add detailed forwarding information
            if message.forward_from:
                # Forwarded from a user who hasn't restricted forwarding privacy
                forward_name = message.forward_from.full_name or message.forward_from.username or f"User {message.forward_from.id}"
                is_bot = "(Bot)" if message.forward_from.is_bot else ""
                metadata += f"\nOriginal sender: {forward_name} {is_bot} (ID: {message.forward_from.id})"
            elif message.forward_sender_name:
                # Forwarded from a user who restricted forwarding privacy
                metadata += f"\nOriginal sender: {message.forward_sender_name} (forwarding privacy enabled)"
            elif message.forward_from_chat:
                # Forwarded from a channel or group
                chat_type = message.forward_from_chat.type.capitalize()
                metadata += f"\nOriginal source: {chat_type} '{message.forward_from_chat.title}' (ID: {message.forward_from_chat.id})"
                if message.forward_signature:
                    metadata += f"\nPost author: {message.forward_signature}"
            
            # Add original message date if available
            if message.forward_date:
                metadata += f"\nOriginal message time: {message.forward_date}"
        else:
            # Regular non-forwarded photo
            metadata = f"Message info: photo from {user_display_name} (User ID: {user.telegram_id})"
        
        metadata += f", Message ID: {message.message_id}, Current time: {message.date}"
        metadata += f"\nPhoto info: dimensions={photo.width}x{photo.height}"
        if message.caption:
            metadata += f"\nCaption: {message.caption}"

        # Add message to history with metadata
        await message_dao.add_message(
            user_id=user.id,
            role=MessageRole.USER,
            text=message.caption,  # Use caption as text if available
            group_id=group_db_id,
            telegram_message_id=message.message_id,
            message_metadata=metadata,
            image_data=photo_data  # Save photo data
        )
        logger.debug(f"Photo message queued for save (user {user.telegram_id}, group_id {group_db_id})")
        
        # Check if we should process this message or wait for more messages
        user_telegram_id = user.telegram_id
        should_process = await message_batcher.register_message(user_telegram_id)
        
        if not should_process:
            # This message is part of a batch, don't respond yet
            logger.info(f"Batching photo message from user {user_telegram_id} - waiting for more messages")
            return
        
        # If we get here, either this is the first message in a batch or the batching period has ended
        # Get message history for context
        if group_db_id is not None:
            message_history = await message_dao.get_group_messages_as_contents(group_id=group_db_id)
            logger.info(f"Retrieved {len(message_history)} messages from group chat history")
        else:
            message_history = await message_dao.get_user_private_messages_as_contents(user_id=user.id)
            logger.info(f"Retrieved {len(message_history)} messages from private chat history")

        if not message_history:
            logger.warning(f"Message history is empty before calling Gemini for user {user.telegram_id}")

        try:
            await message.bot.send_chat_action(chat_id=chat.id, action="typing")
        except Exception as e:
            logger.warning(f"Failed to send chat action 'typing' to {chat.id}: {e}")

        gemini_result = await get_image_response(
            message_history=message_history,
            user=user,
            message=message
        )

        await handle_gemini_result(
            gemini_result,
            message,
            message_dao=message_dao,
            user_dao=user_dao,
            user=user,
            group_db_id=group_db_id
        )

    except Exception as e:
        logger.error(f"Handler error processing photo message for user {user.telegram_id} in chat {chat.id}: {e}", exc_info=True)
        await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки фотографії.")
