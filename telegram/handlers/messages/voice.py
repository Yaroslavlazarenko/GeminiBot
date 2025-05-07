import logging
import io
from typing import Any

from aiogram import F, Router, types, Bot
from aiogram.types import Message
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Assuming these imports are correct paths in your project
from ai.gemini_client import get_audio_response, get_text_response # Assuming you have get_audio_response
from database.models import User, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
from ..utils import send_error_message, get_group_or_none, handle_gemini_result # handle_gemini_result needs adaptation for voice? Or maybe get_audio_response returns similar structure.
# Import the global batcher instance and the callback type
from ..message_batcher import message_batcher, ProcessingCallback

logger = logging.getLogger(__name__)
router = Router()

# --- Actual Processing Logic for Voice Messages ---
# This function is called by the MessageBatcher when the quiet period is met.
# It contains the core logic for handling a voice message.
async def actual_voice_processing_logic(
    bot: Bot,
    message: Message,
    user_dao: UserDAO,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO,
) -> None:
    """
    Performs the actual processing logic for a voice message after batching.
    Downloads, transcribes, fetches history, calls AI, saves response, sends.
    It assumes the incoming message has already been saved to the DB with voice_data.
    """
    chat = message.chat
    user_telegram_id = message.from_user.id
    chat_id = chat.id
    voice = message.voice

    logger.info(f"Starting batched voice processing for user {user_telegram_id} in chat {chat_id} (last message ID: {message.message_id})")

    try:
        # Re-fetch User and Group objects to ensure we have the latest settings
        user = await user_dao.get_user_by_telegram_id(user_telegram_id)
        if not user:
             logger.error(f"User {user_telegram_id} not found in DB during batched voice processing. Cannot proceed.")
             try:
                  await bot.send_message(chat_id=chat_id, text="🤯 Не можу знайти ваші дані для обробки голосового повідомлення. Спробуйте написати знову.")
             except Exception as send_e:
                  logger.error(f"Failed to send user data error message to {chat_id}: {send_e}")
             return # Stop processing

        group = await get_group_or_none(group_dao, chat)
        group_db_id = group.id if group else None

        # Check global/voice response settings again (could have changed)
        if user.is_global_disabled or not getattr(user, 'responds_to_voice', True):
            logger.debug(f"Ignoring batched voice processing for user {user_telegram_id} due to updated user settings.")
            return

        if group and (group.is_global_disabled or not getattr(group, 'responds_to_voice', True)):
             logger.debug(f"Ignoring batched voice processing for user {user_telegram_id} in group {chat_id} due to updated group settings.")
             return

        # --- Download Voice File ---
        # This happens NOW, inside the batched processing
        downloaded_file = None
        transcription_text = None # Will store transcription here

        try:
            file = await bot.get_file(voice.file_id)
            if not file.file_path:
                logger.error(f"File path is missing for voice file_id={voice.file_id} during batched processing.")
                await send_error_message(message, "Помилка: не вдалося отримати шлях до файлу голосового повідомлення (батчинг).")
                return

            downloaded_file = await bot.download_file(file.file_path)
            if downloaded_file is None:
                logger.error(f"Failed to download voice message from path={file.file_path}, received None during batched processing.")
                await send_error_message(message, "Помилка: не вдалося завантажити голосове повідомлення (отримано None, батчинг).")
                return

            voice_data = downloaded_file.read() # Get raw bytes

        except Exception as e:
            logger.error(f"Error downloading voice message {message.message_id} during batched processing: {e}", exc_info=True)
            await send_error_message(message, "Помилка: не вдалося завантажити голосове повідомлення для обробки.")
            return # Cannot proceed without voice data


        # --- Transcribe Voice (if enabled and implemented) ---
        # This also happens NOW
        if getattr(user, 'transcribe_voice_only', False):
            try:
                logger.debug(f"Attempting to transcribe voice message {message.message_id} for user {user_telegram_id}...")
                # TODO: Replace with your actual voice transcription implementation
                # transcription_text = await your_transcription_service.transcribe(voice_data)
                transcription_text = "ГОЛОСОВОЕ СООБЩЕНИЕ (транскрипция временно отключена)" # Placeholder
                logger.debug(f"Transcription result for {message.message_id}: {transcription_text[:100]}...")

                # Optionally, update the saved message in the DB with the transcription
                # so that future history fetches include it.
                if transcription_text:
                     await message_dao.update_message_text(
                         telegram_message_id=message.message_id,
                         chat_id=chat_id, # Need chat_id or group_id/user_id to uniquely identify
                         text=transcription_text
                     )
                     logger.debug(f"Updated DB message {message.message_id} with transcription.")

            except Exception as e:
                logger.error(f"Error transcribing voice message {message.message_id}: {e}", exc_info=True)
                # Decide if processing should stop on transcription failure.
                # For now, let's log and continue without transcription if it fails.
                # await send_error_message(message, "Помилка: не вдалося транскрибувати голосове повідомлення.")
                # return # Uncomment to stop on transcription failure
                transcription_text = f"ГОЛОСОВОЕ СООБЩЕНИЕ (ошибка транскрипции)" # Add placeholder text on error
                logger.warning(f"Transcription failed for message {message.message_id}, proceeding without transcription text.")


        # --- Retrieve Message History ---
        # Get the full history *after* potentially updating the latest message with transcription
        if group_db_id is not None:
            # Assuming get_group_messages_as_contents fetches both text and voice history,
            # including the transcription text we just saved.
            message_history = await message_dao.get_group_messages_as_contents(group_id=group_db_id)
            logger.debug(f"Retrieved {len(message_history)} messages from group chat history for AI.")
        else:
            message_history = await message_dao.get_user_private_messages_as_contents(user_id=user.id) # Use internal user ID
            logger.debug(f"Retrieved {len(message_history)} messages from private chat history for AI.")

        if not message_history:
            logger.warning(f"Message history is unexpectedly empty for user {user_telegram_id} / chat {chat_id} before AI call after batching.")
            return # Nothing to process

        # Send typing or upload_photo action
        try:
            # Use 'typing' or 'upload_voice' if available, or 'upload_document'
             await bot.send_chat_action(chat_id=chat_id, action="typing") # Or "upload_voice" / "upload_document"
        except Exception as e:
            logger.warning(f"Failed to send chat action to {chat_id} during batched voice processing: {e}")

        # --- Call AI Model ---
        # Use get_audio_response if your model handles audio directly,
        # or get_text_response if you rely solely on transcription + text history.
        # Adjust parameter passing based on your AI function's signature.
        gemini_result = await get_text_response( # Or get_audio_response?
            message_history=message_history,
            user=user, # Pass the re-fetched user object
            message=message, # Pass the last voice message object
            # Maybe pass voice_data if get_text_response/get_audio_response needs it?
            # audio_data=voice_data if 'get_audio_response' is used else None
        )

        # --- Handle AI Result (save, send) ---
        # Assuming handle_gemini_result is generic enough or you have a handle_audio_result
        await handle_gemini_result( # Or handle_audio_result?
            gemini_result,
            message, # Pass the last message object (the voice message)
            message_dao=message_dao, # Pass DAOs
            user_dao=user_dao,
            user=user, # Pass the re-fetched user object
            group_db_id=group_db_id # Pass group ID
        )

        logger.info(f"Successfully processed batched voice message for user {user_telegram_id} in chat {chat_id}")

    except Exception as e:
        logger.error(f"Error in batched voice processing logic for user {user_telegram_id} in chat {chat_id} (last message ID: {message.message_id}): {e}", exc_info=True)
        # Use the bot instance passed to this function to send an error message
        try:
            await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки голосового повідомлення після батчинга.")
        except Exception as send_e:
             logger.error(f"Failed to send error message after batched voice processing failure for user {user_telegram_id}: {send_e}")


# --- Handler that uses the Batcher ---
@router.message(F.voice)
async def voice_handler(
    message: Message,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO,
    user_dao: UserDAO,
    user: User,
    session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """
    Handles incoming voice messages. Saves the message (with data) to DB and
    passes it to the message batcher for timed processing.
    """
    chat = message.chat
    user_display_name = message.from_user.full_name or f"User {user.telegram_id}"
    chat_id = chat.id
    user_telegram_id = user.telegram_id

    logger.debug(f"Received voice message {message.message_id} from user {user_display_name} (ID: {user_telegram_id}) in chat {chat_id}. Saving to DB.")

    # --- Preliminary Checks (Do these immediately) ---
    if user.is_global_disabled:
        logger.debug(f"Ignoring voice message from user {user_telegram_id} due to global USER disable.")
        return

    # Get group from DB if this is a group chat
    group = await get_group_or_none(group_dao, chat)
    group_db_id = group.id if group else None # Need group_db_id for saving

    if group and group.is_global_disabled:
        logger.debug(f"Ignoring voice message from user {user_telegram_id} in group {chat_id} due to global GROUP disable.")
        return

    # Check voice message specific settings - if disabled, no need to even save or batch
    if not getattr(user, 'responds_to_voice', True):
        logger.debug(f"Ignoring voice message from user {user_telegram_id} in chat {chat_id} due to USER voice setting.")
        return
    if group and not getattr(group, 'responds_to_voice', True):
        logger.debug(f"Ignoring voice message from user {user_telegram_id} in group chat {chat_id} due to GROUP voice setting.")
        return

    voice = message.voice
    if not voice:
        logger.error(f"Message {message.message_id} marked as voice but no voice object found.")
        await send_error_message(message, "Помилка: некоректні дані голосового повідомлення.")
        return

    # --- Immediate Save to DB ---
    # We save the message immediately including file_id and duration.
    # We DO NOT download the voice data or transcribe it here.
    try:
        # Формируем метаданные для голосового сообщения
        is_forwarded = bool(message.forward_from or message.forward_from_chat or message.forward_sender_name or message.forward_date)

        if is_forwarded:
            metadata = f"Message info: FORWARDED voice message shared by {user_display_name} (User ID: {user.telegram_id})"
            if message.forward_from:
                forward_name = message.forward_from.full_name or message.forward_from.username or f"User {message.forward_from.id}"
                is_bot = "(Bot)" if message.forward_from.is_bot else ""
                metadata += f"\nOriginal sender: {forward_name} {is_bot} (ID: {message.forward_from.id})"
            elif message.forward_sender_name:
                 metadata += f"\nOriginal sender: {message.forward_sender_name} (forwarding privacy enabled)"
            elif message.forward_from_chat:
                chat_type = message.forward_from_chat.type.capitalize()
                metadata += f"\nOriginal source: {chat_type} '{message.forward_from_chat.title}' (ID: {message.forward_from_chat.id})"
                if message.forward_signature:
                    metadata += f"\nPost author: {message.forward_signature}"
            if message.forward_date:
                 metadata += f"\nOriginal message time: {message.forward_date}"
        else:
            metadata = f"Message info: voice message from {user_display_name} (User ID: {user.telegram_id})"

        metadata += f", Duration: {voice.duration}s, File ID: {voice.file_id}, Message ID: {message.message_id}, Current time: {message.date}"
        # Note: transcription_text is added to metadata *after* transcription in the processing logic.

        # Save the message metadata and basic info. Voice data will be fetched *later* by the batcher.
        # Your message_dao.add_message needs to support saving voice file_id/duration and potentially voice_data=None initially.
        # Let's assume add_message can save file_id and duration. voice_data might be too large for standard history rows.
        await message_dao.add_message(
            user_id=user.id, # Use internal DB user ID from middleware
            role=MessageRole.USER,
            text=None, # Transcription text is not available yet
            group_id=group_db_id,
            telegram_message_id=message.message_id,
            message_metadata=metadata # Initial metadata with file_id and duration
            # Do NOT save voice_data here - it's large and processed later
        )
        logger.debug(f"User voice message {message.message_id} saved to DB (user {user_telegram_id}, group_id {group_db_id}) with file_id.")

    except Exception as e:
         logger.error(f"Failed to save user voice message {message.message_id} to DB: {e}", exc_info=True)
         # If saving fails, we cannot reliably process this message later.
         await send_error_message(message, "Не вдалося зберегти ваше голосове повідомлення.")
         return # Cannot proceed if message isn't saved

    # --- Pass to Batcher ---
    try:
        await message_batcher.handle_message(
            message=message,
            processing_callback=actual_voice_processing_logic,
            session_factory=session_factory
        )
        logger.debug(f"Voice message {message.message_id} from user {user_telegram_id} passed to batcher.")
    except Exception as e:
        logger.error(f"Failed to pass message {message.message_id} to batcher: {e}", exc_info=True)
        await send_error_message(message, "Не вдалося обробити ваше повідомлення. Спробуйте пізніше.")