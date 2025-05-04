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
        metadata = f"Message info: text message from {user_display_name} (User ID: {user.telegram_id})"
        if message.forward_from:
            metadata += f" (forwarded from {message.forward_from.first_name + ' ' + message.forward_from.last_name if message.forward_from.last_name else ''})"
        elif message.forward_from_chat:
            metadata += f" (forwarded from channel/group {message.forward_from_chat.title})"
        metadata += f", Message ID: {message.message_id}, Message Time: {message.date}"

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