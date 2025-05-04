import logging

from aiogram import F, Router, types
from aiogram.types import Message, ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from ai.gemini_client import get_audio_response, get_text_response
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
    """Обработчик голосовых сообщений"""
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
            logger.debug(f"Ignoring voice message from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id} due to global USER disable.")
            return
        if group and group.is_global_disabled:
            logger.debug(f"Ignoring voice message from user {user_display_name} (ID: {user.telegram_id}) in group chat {chat.id} due to global GROUP disable.")
            return

        # Check voice message specific settings
        if not getattr(user, 'responds_to_voice', True):
            logger.debug(f"Ignoring voice message from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id} due to USER voice setting.")
            return
        if group and not getattr(group, 'responds_to_voice', True):
            logger.debug(f"Ignoring voice message from user {user_display_name} (ID: {user.telegram_id}) in group chat {chat.id} due to GROUP voice setting.")
            return

        voice = message.voice
        if not voice:
            logger.error("Message marked as voice but no voice object found")
            await send_error_message(message, "Помилка: некоректні дані голосового повідомлення.")
            return

        # Process voice message
        try:
            file = await message.bot.get_file(voice.file_id)
            if not file.file_path:
                logger.error(f"File path is missing for voice file_id={voice.file_id}")
                await send_error_message(message, "Помилка: не вдалося отримати шлях до файлу голосового повідомлення.")
                return

            downloaded_file = await message.bot.download_file(file.file_path)
            if downloaded_file is None:
                logger.error(f"Failed to download voice message from path={file.file_path}, received None")
                await send_error_message(message, "Помилка: не вдалося завантажити голосове повідомлення (отримано None).")
                return

            voice_data = downloaded_file.read()

            # Transcribe if enabled
            transcription_text = None
            if getattr(user, 'transcribe_voice_only', False):
                try:
                    logger.debug("Attempting to transcribe voice message...")
                    # TODO: Add voice transcription implementation
                    pass
                except Exception as e:
                    logger.error(f"Error transcribing voice message: {e}", exc_info=True)
                    await send_error_message(message, "Помилка: не вдалося транскрибувати голосове повідомлення.")
                    return

        except Exception as e:
            logger.error(f"Error processing voice message: {e}", exc_info=True)
            await send_error_message(message, "Помилка: не вдалося обробити голосове повідомлення.")
            return

        # Формируем метаданные для голосового сообщения
        metadata = f"Message info: voice message from {user_display_name} (ID: {user.telegram_id})"
        if message.forward_from:
            metadata += f" (forwarded from {message.forward_from.full_name})"
        elif message.forward_from_chat:
            metadata += f" (forwarded from channel/group {message.forward_from_chat.title})"
        metadata += f", Duration: {voice.duration}s, Message ID: {message.message_id}, Message Time: {message.date}"
        if transcription_text:
            metadata += f"\nTranscription: {transcription_text}"

        # Add message to history with metadata
        await message_dao.add_message(
            user_id=user.id,
            role=MessageRole.USER,
            text=transcription_text,  # Use transcription as text if available
            group_id=group_db_id,
            telegram_message_id=message.message_id,
            metadata=metadata,
            voice_data=voice_data  # Save voice data
        )
        logger.debug(f"Voice message queued for save (user {user.telegram_id}, group_id {group_db_id})")

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
        logger.error(f"Handler error processing voice message for user {user.telegram_id} in chat {chat.id}: {e}", exc_info=True)
        await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки голосового повідомлення.")