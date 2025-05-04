import logging
import io
import tempfile
import os
from PIL import Image
import ffmpeg
from aiogram import F, Router, types
from aiogram.types import Message
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from google.genai import types as gemini_types

# Убедись, что эти импорты корректны в твоем проекте
from ai.gemini_client import get_text_response
from database.models import User, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
from ..utils import send_error_message, get_group_or_none, handle_gemini_result
# Импортируем сам инстанс батчера и тип ProcessingCallback
from ..message_batcher import message_batcher, ProcessingCallback # Добавлено

logger = logging.getLogger(__name__)
router = Router()

# Define FFmpeg paths (оставляем как есть, функция process_video_data не используется в текущем хендлере после батчинга)
FFMPEG_PATH = "/usr/bin/ffmpeg"
FFPROBE_PATH = "/usr/bin/ffprobe"

# Оставляем функцию обработки видео, но не вызываем ее в хендлере для простоты интеграции батчера.
# Ее можно было бы вызвать либо перед сохранением, либо перед транскрипцией, либо перед вызовом ИИ,
# в зависимости от требований к минимальной длительности.
def process_video_data(video_data: bytes) -> bytes:
    """Process video data to ensure minimum duration of 2.0 seconds using ffmpeg."""
    try:
        logger.info(f"Starting video processing. Input size: {len(video_data)} bytes")

        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as input_file, \
             tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as output_file:

            input_file.write(video_data)
            input_file.flush()
            logger.info(f"Created temporary input file: {input_file.name}")

            try:
                probe = ffmpeg.probe(input_file.name, cmd=FFPROBE_PATH)
                duration = float(probe['format']['duration'])
                logger.info(f"Original video duration: {duration:.2f}s")
            except ffmpeg.Error as e:
                logger.error(f"Failed to get video duration: {e.stderr.decode()}")
                os.unlink(input_file.name)
                return video_data # Return original data on error

            if duration >= 2.0:
                logger.info("Video is already long enough, returning original")
                os.unlink(input_file.name)
                return video_data

            target_duration = 2.0
            speed_factor = duration / target_duration
            logger.info(f"Will slow down video by factor {speed_factor:.2f} to reach {target_duration}s")

            try:
                stream = ffmpeg.input(input_file.name)
                video = stream.video
                audio = stream.audio

                video = video.filter('setpts', f'{1/speed_factor}*PTS')

                if audio is not None:
                    atempo_filters = []
                    remaining_speed = speed_factor
                    while remaining_speed < 0.5:
                        atempo_filters.append(0.5)
                        remaining_speed *= 2
                    if remaining_speed != 1.0:
                        atempo_filters.append(remaining_speed)

                    if atempo_filters:
                        audio = audio.filter('atempo', atempo_filters[0])
                        for atempo in atempo_filters[1:]:
                            audio = audio.filter('atempo', atempo)

                if audio is not None:
                    stream = ffmpeg.output(video, audio, output_file.name)
                else:
                    stream = ffmpeg.output(video, output_file.name)

                # Убираем агрессивные настройки качества/битрейта для меньшего файла и более быстрого кодирования
                # stream = stream.global_args('-t', str(target_duration)) # Длительность уже контролируется atempo/setpts
                stream = stream.global_args('-c:v', 'libx264')
                stream = stream.global_args('-preset', 'fast') # Более быстрый пресет
                stream = stream.global_args('-crf', '23') # Среднее качество
                stream = stream.global_args('-maxrate', '1M') # Ограничиваем битрейт
                stream = stream.global_args('-bufsize', '2M')
                stream = stream.global_args('-x264-params', 'keyint=any:scenecut=any') # Не принуждаем keyframes каждый кадр

                if audio is not None:
                    stream = stream.global_args('-c:a', 'aac')
                    stream = stream.global_args('-b:a', '128k') # Средний битрейт аудио

                ffmpeg.run(stream, cmd=FFMPEG_PATH, overwrite_output=True, capture_stdout=True, capture_stderr=True)
                logger.info("Successfully processed video with ffmpeg")

            except ffmpeg.Error as e:
                logger.error(f"ffmpeg processing failed: {e.stderr.decode()}")
                os.unlink(input_file.name)
                return video_data # Return original data on processing error

            with open(output_file.name, 'rb') as f:
                processed_data = f.read()

            os.unlink(input_file.name)
            os.unlink(output_file.name)

            logger.info(f"Video processing complete: original size={len(video_data)}, processed size={len(processed_data)}")
            return processed_data

    except Exception as e:
        logger.error(f"Error processing video: {e}", exc_info=True)
        return video_data # Return original data on any other error


# --- Новая функция обратного вызова для батчера ---
async def _process_video_note_batch_callback(
    bot: types.Bot,
    message: types.Message,
    user_dao: UserDAO,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO
) -> None:
    """
    Callback function executed by the batcher after the quiet period
    for video note processing. This function handles fetching history,
    calling the AI model, and sending the response.
    """
    logger.debug(f"Batch callback triggered for video note message {message.message_id} from user {message.from_user.id} in chat {message.chat.id}")
    try:
        # Re-fetch user and group data inside the callback for safety
        user = await user_dao.get_user_by_telegram_id(message.from_user.id)
        if not user:
             logger.error(f"User {message.from_user.id} not found in DB during video note batch callback!")
             # Cannot proceed without user object. Send error and exit.
             await send_error_message(message, "Помилка: не вдалося знайти дані користувача для обробки відповіді.")
             return # Exit callback

        chat = message.chat
        group = await get_group_or_none(group_dao, chat) if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP] else None
        group_db_id = group.id if group else None

        # Fetch message history, which now includes the saved video note message
        if group_db_id is not None:
            message_history = await message_dao.get_group_messages_as_contents(group_id=group_db_id)
            logger.info(f"Retrieved {len(message_history)} messages from group chat history for batch processing")
        else:
            message_history = await message_dao.get_user_private_messages_as_contents(user_id=user.id)
            logger.info(f"Retrieved {len(message_history)} messages from private chat history for batch processing")

        if not message_history:
             logger.warning(f"Message history is empty before calling Gemini for user {user.telegram_id}")
             # While unexpected if the message was saved, it's possible. Proceed, maybe AI handles empty history.

        # Send typing action to indicate processing
        try:
             await bot.send_chat_action(chat_id=chat.id, action="typing")
        except Exception as e:
             logger.warning(f"Failed to send chat action 'typing' to {chat.id} during batch callback: {e}")

        # Call the AI model
        gemini_result = await get_text_response(
             message_history=message_history,
             user=user, # Pass the fetched user object
             message=message # Pass the original message object
        )

        # Handle the AI model's response
        await handle_gemini_result(
             gemini_result,
             message, # Original message is needed for reply/chat context
             message_dao=message_dao,
             user_dao=user_dao,
             user=user, # Pass the fetched user object
             group_db_id=group_db_id # Pass the group ID
        )
        logger.debug(f"Processing and handling result finished for user {user.telegram_id}, message {message.message_id}")

    except Exception as e:
         logger.error(f"Error in video note batch callback for user {message.from_user.id} in chat {message.chat.id}: {e}", exc_info=True)
         # Send an error message to the user via the original message context
         await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки відео-нотатки після батчинга.")


@router.message(F.video_note)
async def video_note_handler(
    message: Message,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO,
    user_dao: UserDAO,
    user: User # Middleware/Dependencies should provide the User object
) -> None:
    """Обработчик видео-заметок"""
    chat = message.chat
    user_telegram_id = message.from_user.id # Use telegram_id for logging/identification
    user_db_id = user.id # Use user.id for DAO calls

    try:
        # --- PART 1: Immediate Actions (Checks, Download, Save to DB) ---
        group = await get_group_or_none(group_dao, chat) if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP] else None
        group_db_id = group.id if group else None

        user_display_name = message.from_user.full_name or f"User {user_telegram_id}"

        # Check global and video note specific settings immediately
        if user.is_global_disabled or (group and group.is_global_disabled):
            level = "GROUP" if group else "USER"
            logger.debug(f"Ignoring video note from user {user_display_name} (ID: {user_telegram_id}) in chat {chat.id} due to global {level} disable.")
            return

        if not getattr(user, 'responds_to_video_note', True) or (group and not getattr(group, 'responds_to_video_note', True)):
             level = "GROUP" if group else "USER"
             logger.debug(f"Ignoring video note from user {user_display_name} (ID: {user_telegram_id}) in chat {chat.id} due to {level} video note setting.")
             return

        video_note = message.video_note
        if not video_note:
            logger.error("Message marked as video note but no video note object found")
            await send_error_message(message, "Помилка: некоректні дані відео-нотатки.")
            return

        video_data = None # Initialize to None
        transcription_text = None # Initialize transcription text

        # Download and process the video note data
        try:
            file = await message.bot.get_file(video_note.file_id)
            if not file.file_path:
                logger.error(f"File path is missing for video note file_id={video_note.file_id}")
                await send_error_message(message, "Помилка: не вдалося отримати шлях до файлу відео-нотатки.")
                return

            downloaded_file = await message.bot.download_file(file.file_path)
            if downloaded_file is None:
                logger.error(f"Failed to download video note from path={file.file_path}, received None")
                await send_error_message(message, "Помилка: не вдалося завантажити відео-нотатку (отримано None).")
                return
            video_data = downloaded_file.read()

            # TODO: Add video note transcription implementation here if enabled
            if getattr(user, 'transcribe_video_note', False):
                try:
                    logger.debug("Attempting to transcribe video note...")
                    # transcription_text = await your_transcription_service.transcribe(video_data) # Replace with actual call
                    transcription_text = "Транскрипція: (TODO: Implement transcription)" # Placeholder
                    logger.info(f"Transcription result: {transcription_text}")
                except Exception as e:
                    logger.error(f"Error during transcription attempt for video note: {e}", exc_info=True)
                    transcription_text = f"[Transcription failed: {e}]" # Add failure message to metadata/history
                    # Decide if transcription failure should stop processing or just skip transcription
                    # For now, we'll just log and continue without a successful transcription text.


        except Exception as e:
            logger.error(f"Error downloading or preparing video note for saving/transcription: {e}", exc_info=True)
            # This is a critical error preventing saving the message
            await send_error_message(message, "Помилка: не вдалося підготувати відео-нотатку для обробки.")
            return # Stop processing if download fails

        # Formulate metadata for the video note
        is_forwarded = bool(message.forward_from or message.forward_from_chat or message.forward_sender_name or message.forward_date)
        metadata = f"Message info: video note shared by {user_display_name} (User ID: {user_telegram_id})"
        if is_forwarded:
            metadata = f"Message info: FORWARDED video note shared by {user_display_name} (User ID: {user_telegram_id})"
            if message.forward_from: metadata += f"\nOriginal sender: {message.forward_from.full_name or f'User {message.forward_from.id}'} (ID: {message.forward_from.id})"
            elif message.forward_sender_name: metadata += f"\nOriginal sender: {message.forward_sender_name} (privacy enabled)"
            elif message.forward_from_chat: metadata += f"\nOriginal source: {message.forward_from_chat.type.capitalize()} '{message.forward_from_chat.title}' (ID: {message.forward_from_chat.id})"
            if message.forward_signature: metadata += f"\nPost author: {message.forward_signature}"
            if message.forward_date: metadata += f"\nOriginal message time: {message.forward_date}"

        metadata += f", Duration: {video_note.duration}s, Message ID: {message.message_id}, Current time: {message.date}"
        if transcription_text:
            metadata += f"\nTranscription: {transcription_text}" # Include transcription in metadata

        # Save the message to history IMMEDIATELY. The batcher callback needs this entry.
        # If transcription is enabled and successful, save transcription as the message text.
        # Otherwise, text will be None. The AI model can use the video_data.
        await message_dao.add_message(
            user_id=user_db_id, # Use DB user ID from the User object provided by middleware
            role=MessageRole.USER,
            text=transcription_text if (getattr(user, 'transcribe_video_note', False) and transcription_text and not transcription_text.startswith("[Transcription failed")) else None,
            group_id=group_db_id,
            telegram_message_id=message.message_id,
            message_metadata=metadata,
            video_data=video_data # Save the downloaded video note data
        )
        logger.debug(f"Video note message {message.message_id} saved to DB (user {user_telegram_id}, group_id {group_db_id})")

        # --- PART 2: Pass to Batcher ---
        # Instead of checking should_process_message, we *always* pass to the batcher.
        # The batcher decides *when* (or if, if a new message arrives) to call the callback.
        logger.info(f"Passing video note message {message.message_id} from user {user_telegram_id} to batcher.")
        await message_batcher.handle_message(
            message=message, # The original message object
            processing_callback=_process_video_note_batch_callback, # The function to call later
            user_dao=user_dao, # Pass dependencies needed by the callback
            group_dao=group_dao,
            message_dao=message_dao
        )
        logger.debug(f"message_batcher.handle_message called for user {user_telegram_id}")

        # The handler function finishes here. The actual AI processing will happen
        # later if the batcher's timer expires without new messages from this user.

    except Exception as e:
        logger.error(f"Handler error processing video note message for user {user_telegram_id} in chat {chat.id}: {e}", exc_info=True)
        # Ensure an error message is sent for errors occurring *before* passing to batcher
        if video_data is None: # Only send error if we failed before saving/batching
             await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки відео-нотатки.")
        # If an error occurred *after* saving but before batcher.handle_message,
        # it's less critical as the message is saved, and the batcher call might still succeed,
        # or the batch callback itself might catch and report an error.
        # Let's keep the outer catch simple and rely on internal error handling.


# Note: The original check `if should_process:` and the subsequent logic (fetching history,
# sending typing, calling get_text_response, handle_gemini_result) are REMOVED
# from the main `video_note_handler` and moved into the `_process_video_note_batch_callback`.
# The message_batcher.handle_message call REPLACES that entire block.