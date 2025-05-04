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
from ..message_batcher import message_batcher

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

        # Check sticker-specific settings
        if not user.responds_to_sticker:
            logger.debug(f"Ignoring sticker from user {user_display_name} (ID: {user.telegram_id}) due to user sticker setting.")
            return

        if group and not group.responds_to_sticker:
            logger.debug(f"Ignoring sticker from user {user_display_name} (ID: {user.telegram_id}) in group chat {chat.id} due to group sticker setting.")
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

        # Формируем метаданные для стикера с более четким описанием
        metadata_parts = []
        
        is_forwarded = bool(message.forward_from or message.forward_from_chat or message.forward_sender_name or message.forward_date)
        
        if is_forwarded:
            # This is a forwarded sticker
            metadata_parts.append(f"Message info: FORWARDED sticker shared by {user_display_name} (User ID: {user.telegram_id})")
            
            # Add detailed forwarding information
            if message.forward_from:
                # Forwarded from a user who hasn't restricted forwarding privacy
                forward_name = message.forward_from.full_name or message.forward_from.username or f"User {message.forward_from.id}"
                is_bot = "(Bot)" if message.forward_from.is_bot else ""
                metadata_parts.append(f"Original sender: {forward_name} {is_bot} (ID: {message.forward_from.id})")
            elif message.forward_sender_name:
                # Forwarded from a user who restricted forwarding privacy
                metadata_parts.append(f"Original sender: {message.forward_sender_name} (forwarding privacy enabled)")
            elif message.forward_from_chat:
                # Forwarded from a channel or group
                chat_type = message.forward_from_chat.type.capitalize()
                metadata_parts.append(f"Original source: {chat_type} '{message.forward_from_chat.title}' (ID: {message.forward_from_chat.id})")
                if message.forward_signature:
                    metadata_parts.append(f"Post author: {message.forward_signature}")
            
            # Add original message date if available
            if message.forward_date:
                metadata_parts.append(f"Original message time: {message.forward_date}")
        else:
            # Regular non-forwarded sticker
            metadata_parts.append(f"Message info: sticker from {user_display_name} (User ID: {user.telegram_id})")
        
        metadata_parts.append(f"Message ID: {message.message_id}, Current time: {message.date}")
        metadata_parts.append(f"Message Time: {message.date}")

        sticker_info_parts = []
        sticker_info_parts.append(f"emoji: {sticker.emoji}")
        sticker_info_parts.append(f"set_name: {sticker.set_name}")
        if sticker.is_animated:
            sticker_info_parts.append("animated=true")
        if sticker.is_video:
            sticker_info_parts.append("video=true")
        if sticker.custom_emoji_id:
            sticker_info_parts.append(f"custom_emoji_id={sticker.custom_emoji_id}")

        metadata = f"{', '.join(metadata_parts)}\nSticker info: {', '.join(sticker_info_parts)}"

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
        
        # Check if we should process this message or wait for more messages
        user_telegram_id = user.telegram_id
        should_process = await message_batcher.register_message(user_telegram_id)
        
        if not should_process:
            # This message is part of a batch, don't respond yet
            logger.info(f"Batching sticker message from user {user_telegram_id} - waiting for more messages")
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

        # Add specific task hint for sticker responses
        gemini_result = await get_text_response(
            message_history=message_history,
            user=user,
            message=message,
            task_hint="A user has sent a sticker. Understand the sticker's visual content, emoji, and context. Provide a natural, contextually appropriate response that acknowledges the sticker. You can use reactions (emoji) to respond. Keep the response concise and engaging."
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