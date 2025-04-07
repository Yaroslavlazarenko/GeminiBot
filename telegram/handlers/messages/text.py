import logging

from aiogram import F, Router
from aiogram.types import Message
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
    group = await get_group_or_none(group_dao, chat)
    group_db_id = group.id if group else None

    if not user.responds_to_text:
        logger.debug(f"Ignoring text message from user {user.telegram_id} in chat {chat.id} due to USER settings.")
        return
    if group and not group.responds_to_text:
         logger.debug(f"Ignoring text message from user {user.telegram_id} in group chat {chat.id} (DB ID: {group.id}) due to GROUP settings.")
         return

    if not message.text:
        logger.debug(f"Ignoring message without text content from user {user.telegram_id} in chat {chat.id}")
        return

    logger.info(f"Processing text message from user {user.telegram_id} in chat {chat.id} (type: {chat.type}, group_id: {group_db_id})")
    try:
        await message.bot.send_chat_action(chat_id=chat.id, action="typing")
    except (TelegramNetworkError, TelegramBadRequest, TelegramForbiddenError) as e:
         logger.warning(f"Failed to send chat action 'typing' to {chat.id}: {e}")

    try:
        text_to_save = message.text
        if (message.reply_to_message
                and message.reply_to_message.from_user
                and not message.reply_to_message.from_user.is_bot
                and message.reply_to_message.text
                and not message.reply_to_message.audio
                and not message.reply_to_message.voice
                and not message.reply_to_message.photo):
            original_sender = message.reply_to_message.from_user.first_name or f"User_{message.reply_to_message.from_user.id}"
            original_text = message.reply_to_message.text
            reply_text = message.text
            text_to_save = f"User replied: '{reply_text}'\nTo the message from {original_sender}: '{original_text}'"
            logger.debug(f"Formatted reply text for saving: {text_to_save[:100]}...")

        await message_dao.add_message(
            user_id=user.id, role=MessageRole.USER, text=text_to_save, group_id=group_db_id
        )
        logger.debug(f"User message queued for save (user {user.telegram_id}, group_id {group_db_id})")

        if group_db_id is not None:
            message_history = await message_dao.get_group_messages_as_contents(group_id=group_db_id)
        else:
            message_history = await message_dao.get_user_private_messages_as_contents(user_id=user.id)
        logger.debug(f"Fetched {len(message_history)} messages for context (user {user.telegram_id}, group_id {group_db_id})")

        if not message_history:
            logger.warning(f"Message history is empty before calling Gemini for user {user.telegram_id}, group_id {group_db_id}. This might happen after /clear.")

        gemini_result = await get_text_response(message_history=message_history, user=user)

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