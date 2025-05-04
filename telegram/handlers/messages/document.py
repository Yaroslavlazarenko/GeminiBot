import logging
import asyncio
from typing import Dict, List, Optional, Tuple

from aiogram import F, Router
from aiogram.types import Message, Document
from aiogram.enums import ChatType
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
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO,
    user_dao: UserDAO,
    user: User
) -> None:
    """Обработчик документов"""
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
            logger.debug(f"Ignoring document from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id} due to global USER disable.")
            return
        if group and group.is_global_disabled:
            logger.debug(f"Ignoring document from user {user_display_name} (ID: {user.telegram_id}) in group chat {chat.id} due to global GROUP disable.")
            return

        # Check document specific settings
        if not getattr(user, 'responds_to_document', True):
            logger.debug(f"Ignoring document from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id} due to USER document setting.")
            return
        if group and not getattr(group, 'responds_to_document', True):
            logger.debug(f"Ignoring document from user {user_display_name} (ID: {user.telegram_id}) in group chat {chat.id} due to GROUP document setting.")
            return

        document = message.document
        if not document:
            logger.error("Message marked as document but no document object found")
            await send_error_message(message, "Помилка: некоректні дані документа.")
            return

        # Process document
        try:
            file = await message.bot.get_file(document.file_id)
            if not file.file_path:
                logger.error(f"File path is missing for document file_id={document.file_id}")
                await send_error_message(message, "Помилка: не вдалося отримати шлях до файлу документа.")
                return

            downloaded_file = await message.bot.download_file(file.file_path)
            if downloaded_file is None:
                logger.error(f"Failed to download document from path={file.file_path}, received None")
                await send_error_message(message, "Помилка: не вдалося завантажити документ (отримано None).")
                return

            document_data = downloaded_file.read()

        except Exception as e:
            logger.error(f"Error processing document: {e}", exc_info=True)
            await send_error_message(message, "Помилка: не вдалося обробити документ.")
            return

        # Формируем метаданные для документа
        metadata = f"Message info: document from {user_display_name} (ID: {user.telegram_id})"
        if message.forward_from:
            metadata += f" (forwarded from {message.forward_from.full_name})"
        elif message.forward_from_chat:
            metadata += f" (forwarded from channel/group {message.forward_from_chat.title})"
        metadata += f", File name: {document.file_name}, MIME type: {document.mime_type}, "
        metadata += f"Size: {document.file_size} bytes, Message ID: {message.message_id}, Message Time: {message.date}"

        # Add message to history with metadata
        caption_text = message.caption if message.caption else None
        await message_dao.add_message(
            user_id=user.id,
            role=MessageRole.USER,
            text=caption_text,  # Use caption as text if available
            group_id=group_db_id,
            telegram_message_id=message.message_id,
            message_metadata=metadata,
            document_data=document_data  # Save document data
        )
        logger.debug(f"Document message queued for save (user {user.telegram_id}, group_id {group_db_id})")

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

        gemini_result = await get_text_response(
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
        logger.error(f"Handler error processing document for user {user.telegram_id} in chat {chat.id}: {e}", exc_info=True)
        await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки документа.")