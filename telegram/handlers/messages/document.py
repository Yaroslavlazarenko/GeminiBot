import logging
import asyncio
from typing import Dict, List, Optional, Tuple

from aiogram import F, Router
from aiogram.types import Message, Document
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.exc import SQLAlchemyError

from ai.gemini_client import get_text_response
from database.models import User, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
from ..utils import send_error_message, get_group_or_none, handle_gemini_result

logger = logging.getLogger(__name__)
router = Router()

# Supported image MIME types
SUPPORTED_IMAGE_TYPES = {
    'image/png': 'PNG',
    'image/jpeg': 'JPEG',
    'image/heic': 'HEIC',
    'image/heif': 'HEIF'
}

# --- Управление Медиагруппами ---
media_group_cache: Dict[str, List[Tuple[Message, bytes]]] = {}
media_group_timers: Dict[str, asyncio.Task] = {}
MEDIA_GROUP_TIMEOUT = 5.0

async def get_document_data(message: Message) -> Optional[bytes]:
    """Получает данные документа."""
    if not message.document:
        logger.warning(f"No document found in message {message.message_id}")
        return None

    document: Document = message.document
    if not document:
        logger.warning(f"Document object is missing in message {message.message_id}")
        return None

    try:
        logger.debug(f"Attempting to download document file_id={document.file_id} (size {document.file_size} bytes)")
        file = await message.bot.get_file(document.file_id)
        if not file.file_path:
            logger.error(f"File path is missing for document file_id={document.file_id}")
            return None

        downloaded_file = await message.bot.download_file(file.file_path)
        if downloaded_file is None:
            logger.error(f"Failed to download document from path={file.file_path}, received None")
            return None

        document_data = downloaded_file.read()
        logger.debug(f"Downloaded {len(document_data)} bytes for document file_id={document.file_id}")
        return document_data

    except (TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError) as e:
        if "file is too big" in str(e).lower():
            logger.warning(f"Telegram API error downloading document file_id={document.file_id}: File is too big ({document.file_size} bytes). Error: {e}")
        else:
            logger.error(f"Telegram API error downloading document file_id={document.file_id}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error downloading document file_id={document.file_id}: {e}", exc_info=True)
        return None

async def process_media_group(
    media_group_id: str,
    chat_id: int,
    user_telegram_id: int,
    bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Обрабатывает собранную медиагруппу целиком."""
    photos = media_group_cache.get(media_group_id, [])
    if media_group_id in media_group_cache:
        del media_group_cache[media_group_id]
        logger.debug(f"Cleared media group cache for {media_group_id} upon processing start.")
    if media_group_id in media_group_timers:
        del media_group_timers[media_group_id]
        logger.debug(f"Cleared media group timer for {media_group_id} upon processing start.")

    if not photos:
        logger.warning(f"No photos found for media_group_id={media_group_id} when processing started.")
        return

    first_message = photos[0][0]

    async with session_factory() as session:
        user_dao = UserDAO(session)
        group_dao = GroupDAO(session)
        message_dao = MessageHistoryDAO(session)
        logger.info(f"Created new session and DAOs for media group {media_group_id} processing.")

        try:
            user = await user_dao.get_user_by_telegram_id(user_telegram_id)
            if not user:
                try:
                    tg_user_info = await bot.get_chat(user_telegram_id)
                    user = await user_dao.get_or_create_user(
                        telegram_id=user_telegram_id,
                        username=tg_user_info.username or str(user_telegram_id),
                        first_name=tg_user_info.first_name,
                        last_name=tg_user_info.last_name
                    )
                    logger.info(f"User {user_telegram_id} fetched/created within process_media_group session.")
                except Exception as tg_err:
                    logger.error(f"Failed to get Telegram user info for {user_telegram_id}: {tg_err}")
                    user = None

                if not user:
                    logger.error(f"Failed definitively to get or create user {user_telegram_id} within process_media_group session.")
                    await send_error_message(first_message, "Не вдалося обробити ваші дані користувача.")
                    return

            chat = first_message.chat
            group = await get_group_or_none(group_dao, chat)
            group_db_id = group.id if group else None

            logger.info(f"Processing media group {media_group_id} with {len(photos)} photos from user {user.telegram_id} in chat {chat_id}")

            try:
                await bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception as e:
                logger.warning(f"Failed to send chat action 'typing' to {chat_id}: {e}")

            await message_dao.add_message(
                user_id=user.id, role=MessageRole.USER,
                text=f"Message info: next message contains {len(photos)} photos in a media group, Message ID: {first_message.message_id}, Message Time: {first_message.date}",
                group_id=group_db_id,
                telegram_message_id=first_message.message_id
            )

            saved_photo_count = 0
            for msg, photo_data in photos:
                await message_dao.add_message(
                    user_id=user.id, role=MessageRole.USER,
                    image_data=photo_data,
                    group_id=group_db_id,
                    telegram_message_id=msg.message_id
                )
                saved_photo_count += 1
            logger.debug(f"Added info message and {saved_photo_count} photos from media group {media_group_id} to session.")

            message_history = []
            try:
                if group_db_id is not None:
                    message_history = await message_dao.get_group_messages_as_contents(group_id=group_db_id)
                else:
                    message_history = await message_dao.get_user_private_messages_as_contents(user_id=user.id)
                logger.debug(f"Fetched {len(message_history)} messages for context from new session for media group {media_group_id}.")
            except Exception as db_error:
                logger.error(f"Error getting message history in new session for media group {media_group_id}: {db_error}", exc_info=True)

            if not message_history:
                logger.warning(f"Message history is empty before calling Gemini for media group {media_group_id}")

            gemini_result = await get_text_response(
                message_history=message_history,
                user=user,
                message=first_message
            )

            await handle_gemini_result(
                gemini_result, first_message,
                message_dao=message_dao,
                user_dao=user_dao,
                user=user,
                group_db_id=group_db_id
            )

            await session.commit()
            logger.info(f"Successfully processed and committed media group {media_group_id}")

        except SQLAlchemyError as db_err:
            logger.error(f"Database error during process_media_group {media_group_id} for user {user_telegram_id}: {db_err}", exc_info=True)
            await session.rollback()
            logger.warning(f"Rolled back session for process_media_group {media_group_id} due to DB error.")
            await send_error_message(first_message, "Помилка бази даних під час збереження ваших фотографій.")
        except Exception as e:
            logger.error(f"Unexpected error processing media group {media_group_id} for user {user_telegram_id}: {e}", exc_info=True)
            try:
                await session.rollback()
                logger.warning(f"Rolled back session for process_media_group {media_group_id} due to handler error.")
            except Exception as rollback_err:
                logger.error(f"Error during rollback after handler error for media group {media_group_id}: {rollback_err}", exc_info=True)
            await send_error_message(first_message, "🤯 Ой! Сталася неочікувана помилка під час обробки ваших фотографій.")

async def schedule_media_group_processing(
    media_group_id: str,
    chat_id: int,
    user_telegram_id: int,
    bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Планирует или перепланирует обработку медиагруппы через таймаут."""
    if media_group_id in media_group_timers:
        task = media_group_timers[media_group_id]
        if not task.done():
            task.cancel()
            logger.debug(f"Cancelled previous timer for media group {media_group_id}")

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
    user_telegram_id: int,
    bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Ожидает таймаут и запускает обработку медиагруппы."""
    try:
        await asyncio.sleep(MEDIA_GROUP_TIMEOUT)
        logger.info(f"Timeout expired for media group {media_group_id}. Starting processing.")
        await process_media_group(media_group_id, chat_id, user_telegram_id, bot, session_factory)
    except asyncio.CancelledError:
        logger.debug(f"Media group {media_group_id} processing timer cancelled.")
    except Exception as e:
        logger.error(f"Error in delayed media group processing task for {media_group_id}: {e}", exc_info=True)
        if media_group_id in media_group_timers:
            del media_group_timers[media_group_id]

@router.message(F.document)
async def document_handler(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    user_dao: UserDAO,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO
) -> None:
    """Обрабатывает документы, проверяя настройки пользователя и группы."""
    if not message.from_user:
        logger.warning("Received document message without 'from_user'. Ignoring.")
        return

    if not message.document:
        logger.warning(f"No document found in message {message.message_id}")
        return

    # Check if the document is a supported image type
    mime_type = message.document.mime_type
    if mime_type not in SUPPORTED_IMAGE_TYPES:
        logger.debug(f"Ignoring document with unsupported MIME type: {mime_type}")
        return

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
            logger.error(f"Failed to get or create user {message.from_user.id} in document_handler.")
            await send_error_message(message, "Не вдалося знайти або створити ваші дані користувача.")
            return

    chat = message.chat
    group = await get_group_or_none(group_dao, chat)
    group_db_id = group.id if group else None

    if not user.responds_to_photo:
        logger.debug(f"Ignoring document from user {user.telegram_id} in chat {chat.id} due to USER settings.")
        return
    if group and not group.responds_to_photo:
        logger.debug(f"Ignoring document from user {user.telegram_id} in group chat {chat.id} due to GROUP settings.")
        return

    logger.info(f"Processing document ({mime_type}) from user {user.telegram_id} in chat {chat.id} (type: {chat.type}, group_id: {group_db_id})")

    media_group_id = message.media_group_id
    document_data = await get_document_data(message)
    if not document_data:
        await send_error_message(message, "Помилка: не вдалося завантажити ваш документ.")
        return

    if media_group_id:
        logger.debug(f"Document message {message.message_id} is part of media group {media_group_id}. Adding to cache.")
        if media_group_id not in media_group_cache:
            media_group_cache[media_group_id] = []
        media_group_cache[media_group_id].append((message, document_data))
        logger.debug(f"Media group {media_group_id} cache now has {len(media_group_cache[media_group_id])} documents.")

        await schedule_media_group_processing(
            media_group_id=media_group_id,
            chat_id=chat.id,
            user_telegram_id=user.telegram_id,
            bot=message.bot,
            session_factory=session_factory
        )
    else:
        logger.debug(f"Processing single document message {message.message_id}.")
        try:
            await message.bot.send_chat_action(chat_id=chat.id, action="typing")
        except Exception as e:
            logger.warning(f"Failed to send chat action 'typing' to {chat.id}: {e}")

        await message_dao.add_message(
            user_id=user.id, role=MessageRole.USER,
            text=f"Message info: next message is a {SUPPORTED_IMAGE_TYPES[mime_type]} image, Message ID: {message.message_id}, Message Time: {message.date}",
            group_id=group_db_id,
            telegram_message_id=message.message_id
        )
        await message_dao.add_message(
            user_id=user.id, role=MessageRole.USER,
            image_data=document_data,
            group_id=group_db_id,
            telegram_message_id=message.message_id
        )
        logger.debug(f"User single document message {message.message_id} added to middleware session.")

        message_history = []
        try:
            if group_db_id is not None:
                message_history = await message_dao.get_group_messages_as_contents(group_id=group_db_id)
            else:
                message_history = await message_dao.get_user_private_messages_as_contents(user_id=user.id)
            logger.debug(f"Fetched {len(message_history)} messages for single document context.")
        except Exception as db_error:
            logger.error(f"Error getting message history for single document: {db_error}", exc_info=True)

        if not message_history:
            logger.warning(f"Message history is empty before calling Gemini for single document.")

        gemini_result = await get_text_response(
            message_history=message_history,
            user=user,
            message=message
        )

        await handle_gemini_result(
            gemini_result, message,
            message_dao=message_dao,
            user_dao=user_dao,
            user=user,
            group_db_id=group_db_id
        )