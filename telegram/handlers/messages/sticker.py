import logging
import io
from PIL import Image
from aiogram import F, Router
from aiogram.types import Message
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError
from google.genai import types as gemini_types

from ai.gemini_client import get_text_response
from database.models import User, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO, StickerDAO
from ..utils import send_error_message, get_group_or_none, handle_gemini_result

logger = logging.getLogger(__name__)
router = Router()

@router.message(F.sticker)
async def sticker_handler(
    message: Message,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO,
    user_dao: UserDAO,
    sticker_dao: StickerDAO,
    user: User
) -> None:
    """Обработчик стикеров"""
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
            logger.debug(f"Ignoring sticker from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id} due to global USER disable.")
            return
        if group and group.is_global_disabled:
            logger.debug(f"Ignoring sticker from user {user_display_name} (ID: {user.telegram_id}) in group chat {chat.id} due to global GROUP disable.")
            return

        sticker = message.sticker
        if not sticker:
            logger.error("Message marked as sticker but no sticker object found")
            await send_error_message(message, "Помилка: некоректні дані стікера.")
            return

        # Process sticker
        try:
            # Download sticker file
            file = await message.bot.get_file(sticker.file_id)
            if not file.file_path:
                logger.error(f"File path is missing for sticker file_id={sticker.file_id}")
                await send_error_message(message, "Помилка: не вдалося отримати шлях до файлу стікера.")
                return

            downloaded_file = await message.bot.download_file(file.file_path)
            if downloaded_file is None:
                logger.error(f"Failed to download sticker from path={file.file_path}, received None")
                await send_error_message(message, "Помилка: не вдалося завантажити стікер (отримано None).")
                return

            sticker_data = downloaded_file.read()

            # Save or update sticker in database
            sticker_db = await sticker_dao.get_or_create_sticker(
                telegram_sticker_id=sticker.file_unique_id,  # Use file_unique_id as the permanent identifier
                telegram_message_id=message.message_id,
                name=sticker.set_name,
                emoji=sticker.emoji,
                image_data=sticker_data
            )

        except Exception as e:
            logger.error(f"Error processing sticker: {e}", exc_info=True)
            await send_error_message(message, "Помилка: не вдалося обробити стікер.")
            return

        # Формируем метаданные для стикера
        metadata = f"Message info: sticker from {user_display_name} (ID: {user.telegram_id})"
        if message.forward_from:
            metadata += f" (forwarded from {message.forward_from.full_name})"
        elif message.forward_from_chat:
            metadata += f" (forwarded from channel/group {message.forward_from_chat.title})"
        metadata += f", Message ID: {message.message_id}, Message Time: {message.date}"
        metadata += f"\nSticker info: emoji={sticker.emoji}, set_name={sticker.set_name}"
        if sticker.is_animated:
            metadata += ", animated=true"
        if sticker.is_video:
            metadata += ", video=true"
        if sticker.custom_emoji_id:
            metadata += f", custom_emoji_id={sticker.custom_emoji_id}"

        # Add message to history with metadata
        await message_dao.add_message(
            user_id=user.id,
            role=MessageRole.USER,
            text=None,  # No text for stickers
            group_id=group_db_id,
            telegram_message_id=message.message_id,
            message_metadata=metadata,
            sticker_id=sticker_db.id  # Reference to saved sticker
        )
        logger.debug(f"Sticker message queued for save (user {user.telegram_id}, group_id {group_db_id})")

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
        logger.error(f"Handler error processing sticker message for user {user.telegram_id} in chat {chat.id}: {e}", exc_info=True)
        await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки стікера.")