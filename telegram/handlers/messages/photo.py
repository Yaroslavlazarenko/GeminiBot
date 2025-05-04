# telegram/handlers/messages/photo.py

import logging
import asyncio
import os
from typing import Dict, List, Optional, Tuple

from aiogram import F, Router
from aiogram.types import Message, PhotoSize
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.exc import SQLAlchemyError

# Замените 'path.to...' на реальные пути к вашим модулям
from ai.gemini_client import get_text_response
from database.models import User, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
from ..utils import send_error_message, get_group_or_none, handle_gemini_result

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
                text=f"Message info: next message contains {len(photos)} photos in a media group",
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
    session_factory: async_sessionmaker[AsyncSession], 
    user_dao: UserDAO,          
    group_dao: GroupDAO,        
    message_dao: MessageHistoryDAO 
) -> None:
    """
    Обрабатывает фото-сообщения, проверяя настройки пользователя и группы.
    """
    if not message.from_user:
         logger.warning("Received photo message without 'from_user'. Ignoring.")
         return

    # Get user from database
    user = await user_dao.get_user_by_telegram_id(message.from_user.id)
    if not user:
        logger.warning(f"User {message.from_user.id} not found via middleware DAO. Attempting get_or_create.")
        user = await user_dao.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username or str(message.from_user.id),
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )
        if not user:
            logger.error(f"Failed to get or create user {message.from_user.id} in photo_handler.")
            await send_error_message(message, "Не вдалося знайти або створити ваші дані користувача.")
            return

    chat = message.chat
    group = await get_group_or_none(group_dao, chat)
    group_db_id = group.id if group else None

    # Check global response setting first
    if user.is_global_disabled:
        logger.debug(f"Ignoring photo from user {user.telegram_id} in chat {chat.id} due to global USER disable.")
        return
    if group and group.is_global_disabled:
        logger.debug(f"Ignoring photo from user {user.telegram_id} in group chat {chat.id} due to global GROUP disable.")
        return

    # Then check photo-specific setting
    if not getattr(user, 'responds_to_photo', True):
        logger.debug(f"Ignoring photo from user {user.telegram_id} in chat {chat.id} due to USER photo setting.")
        return
    if group and not getattr(group, 'responds_to_photo', True):
        logger.debug(f"Ignoring photo from user {user.telegram_id} in group chat {chat.id} due to GROUP photo setting.")
        return

    media_group_id = message.media_group_id
    photo_result = await get_best_photo_data(message)
    if not photo_result:
        await send_error_message(message, "Помилка: не вдалося завантажити ваше фото.")
        return

    photo_data = photo_result

    if media_group_id:
        # --- Обработка МЕДИАГРУППЫ ---
        logger.debug(f"Photo message {message.message_id} is part of media group {media_group_id}. Adding to cache.")
        if media_group_id not in media_group_cache:
            media_group_cache[media_group_id] = []
        media_group_cache[media_group_id].append((message, photo_data))
        logger.debug(f"Media group {media_group_id} cache now has {len(media_group_cache[media_group_id])} photos.")

        # Планируем/перепланируем отложенную задачу `process_media_group`.
        # Ключевой момент: передаем `session_factory`, чтобы `process_media_group`
        # могла создать свою СОБСТВЕННУЮ сессию БД для атомарной обработки всей группы.
        # НЕЛЬЗЯ передавать `user_dao`, `group_dao`, `message_dao` из middleware,
        # так как их сессия будет закрыта к моменту выполнения фоновой задачи.
        await schedule_media_group_processing(
            media_group_id=media_group_id,
            chat_id=chat.id,
            user_telegram_id=user.telegram_id, # Передаем ID, а не объект User
            bot=message.bot,
            session_factory=session_factory # Передаем фабрику сессий
        )
        # Завершаем работу хендлера для этого фото. Middleware закроет СВОЮ сессию.
        # Основная работа будет сделана в фоновой задаче process_media_group.

    else:
        # --- Обработка ОДИНОЧНОГО ФОТО ---
        # Здесь все просто: используем сессию и DAO, предоставленные middleware.
        # Commit/Rollback будет выполнен самим middleware в конце обработки этого сообщения.
        logger.debug(f"Processing single photo message {message.message_id}.")
        try:
            await message.bot.send_chat_action(chat_id=chat.id, action="typing")
        except Exception as e:
            logger.warning(f"Failed to send chat action 'typing' to {chat.id}: {e}")

        # Используем DAO из middleware (сессия middleware)
        await message_dao.add_message( # Используем message_dao
            user_id=user.id, role=MessageRole.USER, text="Message info: next message is a photo",
            group_id=group_db_id, telegram_message_id=message.message_id
        )
        await message_dao.add_message( # Используем message_dao
            user_id=user.id, role=MessageRole.USER, image_data=photo_data,
            group_id=group_db_id, telegram_message_id=message.message_id
        )
        logger.debug(f"User single photo message {message.message_id} added to middleware session.")

        message_history = []
        try:
            if group_db_id is not None:
                message_history = await message_dao.get_group_messages_as_contents(group_id=group_db_id) # Используем message_dao
            else:
                message_history = await message_dao.get_user_private_messages_as_contents(user_id=user.id) # Используем message_dao
            logger.debug(f"Fetched {len(message_history)} messages for single photo context.")
        except Exception as db_error:
            logger.error(f"Error getting message history for single photo: {db_error}", exc_info=True)

        if not message_history:
            logger.warning(f"Message history is empty before calling Gemini for single photo.")

        gemini_result = await get_text_response(
            message_history=message_history, 
            user=user,
            message=message
        )

        # Обрабатываем результат от AI (передаем DAO из middleware)
        # Ответ модели будет добавлен в ТУ ЖЕ сессию middleware
        await handle_gemini_result(
            gemini_result, message,
            message_dao=message_dao, # Используем message_dao
            user_dao=user_dao,       # Используем user_dao
            user=user,
            group_db_id=group_db_id
        )
        # Commit/Rollback для этой сессии будет выполнен middleware после завершения этого хендлера.
