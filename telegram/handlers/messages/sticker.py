import logging
import io
from PIL import Image
from aiogram import F, Router
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError
from google.genai import types as gemini_types

from ai.gemini_client import get_text_response
from database.models import User, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO, StickerDAO
from ..utils import send_error_message, get_group_or_none, handle_gemini_result

logger = logging.getLogger(__name__)
router = Router()

async def process_sticker_data(sticker, message) -> bytes | None:
    """Process sticker data, ensuring it meets WebP requirements."""
    try:
        file = await message.bot.get_file(sticker.file_id)
        if not file.file_path:
            logger.error(f"File path is missing for sticker file_id={sticker.file_id}")
            return None

        # Download sticker file
        downloaded_file = await message.bot.download_file(file.file_path)
        if downloaded_file is None:
            logger.error(f"Failed to download sticker from path={file.file_path}")
            return None

        sticker_data = downloaded_file.read()

        # For WebP stickers, verify dimensions and format
        if sticker.mime_type == "image/webp":
            try:
                img = Image.open(io.BytesIO(sticker_data))
                width, height = img.size
                logger.info(f"Processing WebP sticker: {width}x{height} pixels")

                # Verify that either width or height is 512px for proper display
                if width != 512 and height != 512:
                    logger.warning(f"Sticker dimensions ({width}x{height}) don't match Telegram requirements")
                    # We'll still process it, just log the warning
            except Exception as e:
                logger.error(f"Error processing WebP sticker: {e}")
                return None

        return sticker_data

    except (TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError) as e:
        logger.error(f"Telegram API error downloading sticker: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error processing sticker: {e}", exc_info=True)
        return None

@router.message(F.sticker)
async def sticker_handler(
    message: Message,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO,
    user_dao: UserDAO,
    sticker_dao: StickerDAO,
    user: User
) -> None:
    """Handles sticker messages, processing WebP stickers and other sticker types."""
    if not message.from_user:
        logger.warning("Received sticker message without 'from_user'. Ignoring.")
        return

    chat = message.chat
    group = await get_group_or_none(group_dao, chat)
    group_db_id = group.id if group else None

    # Get user display name for better identification
    user_display_name = message.from_user.full_name
    if not user_display_name:
        user_display_name = f"User {user.telegram_id}"

    # Check if message is forwarded
    is_forwarded = message.forward_from is not None or message.forward_from_chat is not None
    if is_forwarded:
        original_sender = message.forward_from
        if original_sender:
            logger.debug(f"Processing forwarded sticker from original sender {original_sender.full_name} (ID: {original_sender.id})")
        else:
            logger.debug("Processing forwarded sticker from unknown original sender")

    # Check global response setting first
    if user.is_global_disabled:
        logger.debug(f"Ignoring sticker from user {user_display_name} (ID: {user.telegram_id}) due to global USER disable.")
        return
    if group and group.is_global_disabled:
        logger.debug(f"Ignoring sticker from user {user_display_name} (ID: {user.telegram_id}) in group chat {chat.id} due to global GROUP disable.")
        return

    sticker = message.sticker
    if not sticker:
        logger.error(f"No sticker data in message from user {user_display_name} (ID: {user.telegram_id})")
        return

    # Process the sticker
    sticker_data = await process_sticker_data(sticker, message)
    if not sticker_data:
        await send_error_message(message, "Помилка: не вдалося обробити стікер.")
        return

    try:
        # Send typing indicator
        await message.bot.send_chat_action(chat_id=chat.id, action="typing")
    except Exception as e:
        logger.warning(f"Failed to send chat action 'typing' to {chat.id}: {e}")

    try:
        # Create or get existing sticker
        db_sticker = await sticker_dao.get_or_create_sticker(
            telegram_sticker_id=sticker.file_id,
            telegram_message_id=message.message_id,
            name=sticker.set_name,
            emoji=sticker.emoji,
            image_data=sticker_data
        )

        # Add message info first
        message_info = f"Message info: next message is a sticker from {user_display_name}"
        if is_forwarded and original_sender:
            message_info += f" (forwarded from {original_sender.full_name}, message ID: {message.message_id}, message Time: {message.date})"
        elif is_forwarded:
            message_info += " (forwarded from unknown user)"

        await message_dao.add_message(
            user_id=user.id,
            role=MessageRole.USER,
            text=message_info,
            group_id=group_db_id,
            telegram_message_id=message.message_id,
            sticker_id=db_sticker.id
        )

        logger.info(f"Sticker message saved (user {user_display_name} (ID: {user.telegram_id}), group_id {group_db_id})")

        # Get message history for context
        message_history = []
        try:
            if group_db_id is not None:
                message_history = await message_dao.get_group_messages_as_contents(group_id=group_db_id, limit=500)
                logger.info(f"Retrieved {len(message_history)} messages from group history")
                
                # Add group context to the first message
                if message_history:
                    group_context_text = (
                        f"Group context: This is a group chat named '{group.name}'. "
                        f"Total messages in history: {len(message_history)}. "
                        "Please analyze the sticker and provide a relevant response considering the group context."
                    )
                    group_context = gemini_types.Content(
                        parts=[gemini_types.Part(text=group_context_text)],
                        role="user"
                    )
                    message_history = [group_context] + message_history
            else:
                message_history = await message_dao.get_user_private_messages_as_contents(user_id=user.id)
                logger.info(f"Retrieved {len(message_history)} messages from private chat history")

        except Exception as e:
            logger.error(f"Error getting message history: {e}", exc_info=True)
            await send_error_message(message, "Помилка: не вдалося отримати історію повідомлень.")
            return

        logger.info(f"Calling AI for sticker from user {user_display_name} (ID: {user.telegram_id}), group_id {group_db_id}")

        try:
            # Get AI response
            gemini_result = await get_text_response(
                message_history=message_history,
                user=user,
                message=message
            )

            # Handle the response
            await handle_gemini_result(
                gemini_result,
                message,
                message_dao=message_dao,
                user_dao=user_dao,
                user=user,
                group_db_id=group_db_id
            )

        except Exception as e:
            logger.error(f"Error getting AI response for sticker: {e}", exc_info=True)
            await send_error_message(message, "Помилка під час обробки стікера. Спробуйте пізніше.")
            return

    except Exception as e:
        logger.error(f"Handler error processing sticker for user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id}: {e}", exc_info=True)
        await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки вашого стікера.")