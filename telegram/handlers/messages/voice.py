import logging

from aiogram import F, Router
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError

from ai.gemini_client import get_audio_response
from database.models import User, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
from ..utils import send_error_message, get_group_or_none, handle_gemini_result

logger = logging.getLogger(__name__)
router = Router()

@router.message(F.voice)
async def voice_handler(
    message: Message,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO,
    user_dao: UserDAO,
    user: User
) -> None:
    """Обрабатывает голосовые сообщения, проверяя настройки пользователя и группы."""
    chat = message.chat
    group = await get_group_or_none(group_dao, chat)
    group_db_id = group.id if group else None

    # Validate user data
    if not user:
        logger.error(f"User object is None for message {message.message_id}")
        await send_error_message(message, "Помилка: не вдалося отримати дані користувача.")
        return

    if not user.telegram_id:
        logger.error(f"User {user.id} has no telegram_id")
        await send_error_message(message, "Помилка: не вдалося ідентифікувати користувача.")
        return

    # Get user display name for better identification
    user_display_name = message.from_user.full_name
    if not user_display_name:
        user_display_name = f"User {user.telegram_id}"

    # Check if message is forwarded
    is_forwarded = message.forward_from is not None or message.forward_from_chat is not None
    if is_forwarded:
        original_sender = message.forward_from
        if original_sender:
            logger.debug(f"Processing forwarded voice message from original sender {original_sender.full_name} (ID: {original_sender.id})")
        else:
            logger.debug(f"Processing forwarded voice message from unknown original sender")

    if not user.responds_to_voice:
        logger.debug(f"Voice messages disabled for user {user_display_name} (ID: {user.telegram_id})")
        return

    if group and not group.responds_to_voice:
        logger.debug(f"Voice messages disabled for group {group.name} (ID: {group.telegram_chat_id})")
        return

    voice = message.voice
    if not voice:
        logger.error(f"Voice object is None for message {message.message_id}")
        await send_error_message(message, "Помилка: не вдалося отримати голосове повідомлення.")
        return

    try:
        file = await message.bot.get_file(voice.file_id)
        if not file.file_path:
            logger.error(f"File path is missing for voice file_id={voice.file_id}")
            await send_error_message(message, "Помилка: не вдалося отримати шлях до файлу.")
            return
        downloaded_file = await message.bot.download_file(file.file_path)
        if downloaded_file is None:
            logger.error(f"Failed to download voice file from path={file.file_path}, received None.")
            await send_error_message(message, "Помилка: не вдалося завантажити голосове повідомлення (отримано None).")
            return
        audio_bytes = downloaded_file.read()
    except (TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError) as e:
        logger.error(f"Telegram API error downloading voice file: {e}", exc_info=True)
        await send_error_message(message, f"Помилка мережі або API Telegram під час завантаження: {e}.")
        return
    except Exception as e:
        logger.error(f"Unexpected error downloading voice file: {e}", exc_info=True)
        await send_error_message(message, "Несподівана помилка при завантаженні файлу.")
        return

    if not audio_bytes:
        logger.error(f"Audio data is empty after download attempt for user {user.telegram_id}")
        return

    try:
        await message_dao.add_message(
            user_id=user.id, role=MessageRole.USER, text="Message info: next message is audio message", group_id=group_db_id,
            telegram_message_id=message.message_id
        )
        await message_dao.add_message(
            user_id=user.id, role=MessageRole.USER, audio_data=audio_bytes, group_id=group_db_id,
            telegram_message_id=message.message_id
        )
        logger.debug(f"User voice message queued for save (user {user.telegram_id}, group_id {group_db_id})")

        if group_db_id is not None:
            message_history = await message_dao.get_group_messages_as_contents(group_id=group_db_id)
        else:
            message_history = await message_dao.get_user_private_messages_as_contents(user_id=user.id)
        logger.debug(f"Fetched {len(message_history)} messages for context (user {user.telegram_id}, group_id {group_db_id})")

        if not message_history:
             logger.warning(f"Message history is empty before calling Gemini for user {user.telegram_id}, group_id {group_db_id} (voice).")

        generate_full_response = not user.transcribe_voice_only
        logger.debug(f"Calling AI for voice. Generate response based on user setting: {generate_full_response} (user {user.telegram_id}, group_id {group_db_id})")

        gemini_result = await get_audio_response(
            message_history=message_history,
            user=user,
            response=generate_full_response
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
        logger.error(f"Handler error processing voice message for user {user.telegram_id} in chat {chat.id}: {e}", exc_info=True)
        await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки вашого голосового повідомлення.")