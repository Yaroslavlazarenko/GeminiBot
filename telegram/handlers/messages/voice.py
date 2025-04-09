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

    if not user.responds_to_voice:
        logger.debug(f"Ignoring voice message from user {user.telegram_id} in chat {chat.id} due to USER settings.")
        return
    if group and not group.responds_to_voice:
        logger.debug(f"Ignoring voice message from user {user.telegram_id} in group chat {chat.id} (DB ID: {group.id}) due to GROUP settings.")
        return

    if not message.voice:
        logger.warning(f"Voice message object is missing for user {user.telegram_id} in chat {chat.id}")
        return

    logger.info(f"Processing voice message from user {user.telegram_id} in chat {chat.id} (type: {chat.type}, group_id: {group_db_id})")
    try:
        await message.bot.send_chat_action(chat_id=chat.id, action="typing")
    except Exception as inner_e:
        logger.warning(f"Failed to send fallback chat action 'typing' to {chat.id}: {inner_e}")

    voice = message.voice
    audio_bytes: bytes | None = None

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
        logger.debug(f"Downloaded {len(audio_bytes)} bytes for voice message from user {user.telegram_id} in chat {chat.id}")
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
            user_id=user.id, role=MessageRole.USER, text="Message info: next message is audio message", group_id=group_db_id
        )
        await message_dao.add_message(
            user_id=user.id, role=MessageRole.USER, audio_data=audio_bytes, group_id=group_db_id
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