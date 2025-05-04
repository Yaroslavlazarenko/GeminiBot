import logging

from aiogram import F, Router
from aiogram.types import Message
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError

from ai.gemini_client import get_text_response
from database.models import User, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
from ..utils import send_error_message, get_group_or_none, handle_gemini_result

logger = logging.getLogger(__name__)
router = Router()

@router.message(F.text)
async def text_handler(
    message: Message,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO,
    user_dao: UserDAO,
    user: User
) -> None:
    """Обрабатывает текстовые сообщения, проверяя настройки пользователя и группы."""
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
            logger.debug(f"Ignoring text message from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id} due to global USER disable.")
            return
        if group and group.is_global_disabled:
            logger.debug(f"Ignoring text message from user {user_display_name} (ID: {user.telegram_id}) in group chat {chat.id} due to global GROUP disable.")
            return

        # Then check text-specific setting
        if not getattr(user, 'responds_to_text', True):
            logger.debug(f"Ignoring text message from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id} due to USER text setting.")
            return
        if group and not getattr(group, 'responds_to_text', True):
            logger.debug(f"Ignoring text message from user {user_display_name} (ID: {user.telegram_id}) in group chat {chat.id} due to GROUP text setting.")
            return

        # Формируем метаданные для текстового сообщения
        is_forwarded = bool(message.forward_from or message.forward_from_chat or message.forward_sender_name or message.forward_date)
        
        if is_forwarded:
            # This is a forwarded message
            metadata = f"Message info: FORWARDED text message shared by {user_display_name} (User ID: {user.telegram_id})"
            
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
            # Regular non-forwarded message
            metadata = f"Message info: text message from {user_display_name} (User ID: {user.telegram_id})"
        
        metadata += f", Message ID: {message.message_id}, Current time: {message.date}"

        # Add message to history with metadata
        await message_dao.add_message(
            user_id=user.id,
            role=MessageRole.USER,
            text=message.text,
            group_id=group_db_id,
            telegram_message_id=message.message_id,
            message_metadata=metadata
        )
        logger.debug(f"User message queued for save (user {user.telegram_id}, group_id {group_db_id})")

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
        logger.error(f"Handler error processing text message for user {user.telegram_id} in chat {chat.id}: {e}", exc_info=True)
        await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки вашого текстового повідомлення.")