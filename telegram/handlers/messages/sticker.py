import logging
import io
import tempfile
import os
from PIL import Image # Not used in process_video_data, but kept as it's in the imports
import ffmpeg
from aiogram import F, Router, Bot
from aiogram.types import Message
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database.models import User, MessageRole # Keep necessary imports
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO, StickerDAO # Keep necessary imports
from ai.gemini_client import get_text_response # Keep necessary imports
from ..utils import send_error_message, get_group_or_none, handle_gemini_result # Keep necessary imports
from ..message_batcher import message_batcher, ProcessingCallback # Keep necessary imports

logger = logging.getLogger(__name__)
router = Router()

# Define FFmpeg paths - ensure these are correct for your environment
FFMPEG_PATH = "/usr/bin/ffmpeg"
FFPROBE_PATH = "/usr/bin/ffprobe"

def process_video_data(video_data: bytes) -> bytes:
    """
    Process video data to ensure minimum duration of 2.0 seconds using ffmpeg.
    Prioritizes quality over speed using H.264/AAC encoding.
    """
    target_duration = 2.0
    processed_data = video_data # Default return value in case of errors or no processing needed

    try:
        logger.info(f"Starting video processing. Input size: {len(video_data)} bytes")

        # Use .webm for input as Telegram videos are often webm, .mp4 for output (H.264/AAC standard)
        with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as input_file, \
             tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as output_file:

            input_file.write(video_data)
            input_file.flush()
            input_filename = input_file.name
            output_filename = output_file.name

            logger.info(f"Created temporary input file: {input_filename}")
            logger.info(f"Created temporary output file: {output_filename}")

            try:
                probe = ffmpeg.probe(input_filename, cmd=FFPROBE_PATH)
                # Get duration from format or video stream
                duration_str = probe['format'].get('duration')
                if not duration_str:
                     # Sometimes duration is only in the stream info
                     video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
                     if video_stream:
                         duration_str = video_stream.get('duration')

                if duration_str:
                    duration = float(duration_str)
                    logger.info(f"Original video duration: {duration:.2f}s")
                else:
                    logger.warning(f"Could not determine video duration from probe data for {input_filename}. Skipping processing.")
                    # Clean up temp files before returning original data
                    if os.path.exists(input_filename): os.unlink(input_filename)
                    if os.path.exists(output_filename): os.unlink(output_filename)
                    return processed_data

            except ffmpeg.Error as e:
                logger.error(f"Failed to get video duration using ffprobe for {input_filename}: {str(e)}")
                # Clean up temp files before returning original data
                if os.path.exists(input_filename): os.unlink(input_filename)
                if os.path.exists(output_filename): os.unlink(output_filename)
                return processed_data
            except Exception as e:
                 logger.error(f"An unexpected error occurred during ffprobe for {input_filename}: {str(e)}", exc_info=True)
                 # Clean up temp files before returning original data
                 if os.path.exists(input_filename): os.unlink(input_filename)
                 if os.path.exists(output_filename): os.unlink(output_filename)
                 return processed_data


            if duration >= target_duration:
                logger.info(f"Video duration ({duration:.2f}s) is >= {target_duration}s, returning original data.")
                # Clean up input temp file as it's no longer needed
                if os.path.exists(input_filename): os.unlink(input_filename)
                # Ensure output temp file is also cleaned up
                if os.path.exists(output_filename): os.unlink(output_filename)
                return processed_data

            # Calculate the factor needed to *slow down* the video/audio
            # To reach a target duration T from original duration D (where D < T),
            # the playback speed needs to be D/T.
            speed_reduction_factor = duration / target_duration
            logger.info(f"Will slow down video/audio by factor {speed_reduction_factor:.3f} to reach {target_duration}s")

            try:
                # Input stream
                stream = ffmpeg.input(input_filename)
                video = stream.video
                audio = stream.audio # This will be None if no audio stream exists

                # Apply video slowdown filter (setpts factor is the inverse of speed factor)
                video = video.filter('setpts', f'{1/speed_reduction_factor}*PTS')
                logger.debug(f"Applied video filter: setpts={1/speed_reduction_factor}*PTS")

                # Apply audio slowdown filters using atempo chaining if audio exists
                if audio is not None:
                    atempo_factors = []
                    # Start with the desired speed reduction factor for audio
                    remaining_speed_factor = speed_reduction_factor

                    # Chain atempo filters as needed (each can only handle factors 0.5 to 2.0)
                    # If we need to slow down by a factor < 0.5, repeatedly apply atempo=0.5
                    while remaining_speed_factor < 0.5:
                        atempo_factors.append(0.5)
                        remaining_speed_factor /= 0.5 # Calculate the remaining factor needed

                    # Apply the remaining factor (will be >= 0.5 and <= 1.0 since original < target)
                    if remaining_speed_factor > 0: # Avoid issues if calculation resulted in 0 or negative
                         atempo_factors.append(remaining_speed_factor)
                    else:
                         logger.warning(f"Calculated invalid remaining_speed_factor: {remaining_speed_factor}. Skipping audio processing.")
                         audio = None # Disable audio if cannot process

                    if atempo_factors:
                        logger.debug(f"Applying audio atempo factors: {atempo_factors}")
                        audio_stream = audio
                        for factor in atempo_factors:
                            audio_stream = audio_stream.filter('atempo', factor)
                        audio = audio_stream # Update the audio variable with the filtered stream
                    # If atempo_factors is empty, speed_reduction_factor was already >= 0.5 (e.g., duration 1.5s),
                    # and remaining_speed_factor will be the original, which is handled by the single append.
                    # The logic seems robust for factors between 0 and 1.

                # Configure output stream with video and potentially audio
                if audio is not None:
                    stream = ffmpeg.output(video, audio, output_filename)
                else:
                    stream = ffmpeg.output(video, output_filename, an=None) # an=None explicitly says no audio if it didn't exist

                # Apply user's specified global args for quality and encoding
                stream = stream.global_args('-c:v', 'libx264') # Video codec
                stream = stream.global_args('-preset', 'fast') # Encoding speed/compression efficiency (fast is a good balance)
                stream = stream.global_args('-crf', '23') # Constant Rate Factor (quality). Lower is better quality (0-51), 23 is good default.
                stream = stream.global_args('-maxrate', '1M') # Max video bitrate (1 Mbps) - helps control file size while maintaining quality
                stream = stream.global_args('-bufsize', '2M') # Buffer size for maxrate
                # stream = stream.global_args('-x264-params', 'keyint=any:scenecut=any') # Potentially useful for seeking, but 'any' can hurt compression

                # Apply user's specified audio args if audio exists
                if audio is not None:
                    stream = stream.global_args('-c:a', 'aac') # Audio codec
                    stream = stream.global_args('-b:a', '128k') # Audio bitrate

                # Force output format to mp4
                stream = stream.global_args('-f', 'mp4')

                logger.debug(f"Executing ffmpeg command: {ffmpeg.compile(stream, cmd=FFMPEG_PATH)}")

                # Run ffmpeg command
                stdout, stderr = ffmpeg.run(
                    stream,
                    cmd=FFMPEG_PATH,
                    overwrite_output=True,
                    capture_stdout=True,
                    capture_stderr=True
                )
                logger.info("Successfully processed video with ffmpeg")
                if stdout: logger.debug(f"ffmpeg stdout: {stdout.decode('utf-8')}")
                if stderr: logger.debug(f"ffmpeg stderr: {stderr.decode('utf-8')}")

                # Read processed data from the output file
                with open(output_filename, 'rb') as f:
                    processed_data = f.read()

                logger.info(f"Video processing complete: original size={len(video_data)}, processed size={len(processed_data)}")

            except ffmpeg.Error as e:
                logger.error(f"ffmpeg processing failed for {input_filename}: stdout={e.stdout.decode('utf-8')}, stderr={e.stderr.decode('utf-8')}")
                # Return original data on ffmpeg failure
                processed_data = video_data
            except Exception as e:
                logger.error(f"An unexpected error occurred during ffmpeg processing for {input_filename}: {str(e)}", exc_info=True)
                # Return original data on any other failure
                processed_data = video_data
            finally:
                # Clean up temporary files
                if os.path.exists(input_filename):
                    os.unlink(input_filename)
                    logger.debug(f"Cleaned up input temp file: {input_filename}")
                if os.path.exists(output_filename):
                    os.unlink(output_filename)
                    logger.debug(f"Cleaned up output temp file: {output_filename}")


    except Exception as e:
        logger.error(f"Error setting up video processing: {str(e)}", exc_info=True)
        # Return original data if temp file creation or initial setup fails
        processed_data = video_data

    return processed_data

# --- Rest of your handler code remains the same ---

# Define the ProcessingCallback type alias
# ProcessingCallback = Callable[[Bot, Message, UserDAO, GroupDAO, MessageHistoryDAO], Awaitable[None]]
# message_batcher = MessageBatcher(processing_delay=1.0) # Assuming MessageBatcher is defined elsewhere

# --- Функция обратного вызова для батчера стикеров ---
async def actual_sticker_processing_logic(
    bot: Bot,
    message: Message,
    user_dao: UserDAO,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO,
) -> None:
    """Выполняет фактическую обработку стикера после батчинга.
    Получает историю сообщений, вызывает AI, сохраняет и отправляет ответ.
    """
    chat = message.chat
    user_telegram_id = message.from_user.id
    chat_id = chat.id

    logger.info(f"Starting batched sticker processing for user {user_telegram_id} in chat {chat_id} (last message ID: {message.message_id})")

    try:
        # Получаем актуальные данные пользователя и группы
        user = await user_dao.get_user_by_telegram_id(user_telegram_id)
        if not user:
            logger.error(f"User {user_telegram_id} not found in DB during batched sticker processing. Cannot proceed.")
            try:
                await bot.send_message(chat_id=chat_id, text="🤯 Не можу знайти ваші дані для обробки стікера. Спробуйте написати знову.")
            except Exception as send_e:
                logger.error(f"Failed to send user data error message to {chat_id}: {send_e}")
            return

        group = await get_group_or_none(group_dao, chat)
        group_db_id = group.id if group else None

        # Проверяем настройки ответов на стикеры
        # Re-check settings here just in case they changed during batching wait time
        if user.is_global_disabled or not getattr(user, 'responds_to_stickers', True):
            logger.debug(f"Ignoring batched sticker processing for user {user_telegram_id} due to updated user settings.")
            return

        if group and (group.is_global_disabled or not getattr(group, 'responds_to_stickers', True)):
            logger.debug(f"Ignoring batched sticker processing for user {user_telegram_id} in group {chat_id} due to updated group settings.")
            return

        # Получаем историю сообщений
        if group_db_id is not None:
            message_history = await message_dao.get_group_messages_as_contents(group_id=group_db_id)
            logger.debug(f"Retrieved {len(message_history)} messages from group chat history for AI.")
        else:
            message_history = await message_dao.get_user_private_messages_as_contents(user_id=user.id)
            logger.debug(f"Retrieved {len(message_history)} messages from private chat history for AI.")

        if not message_history:
            logger.warning(f"Message history is unexpectedly empty for user {user_telegram_id} / chat {chat_id} before AI call after batching.")
            return

        # Отправляем индикатор набора текста
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception as e:
            # This is non-critical, just log
            logger.warning(f"Failed to send chat action to {chat_id} during batched sticker processing: {e}")

        # Вызываем AI модель
        gemini_result = await get_text_response(
            message_history=message_history,
            user=user,
            message=message # Pass the original message object
        )

        # Обрабатываем результат AI
        await handle_gemini_result(
            gemini_result,
            message, # Pass the original message object
            message_dao=message_dao,
            user_dao=user_dao,
            user=user,
            group_db_id=group_db_id
        )

        logger.info(f"Successfully processed batched sticker message for user {user_telegram_id} in chat {chat_id}")

    except Exception as e:
        logger.error(f"Error in batched sticker processing logic for user {user_telegram_id} in chat {chat_id} (last message ID: {message.message_id}): {e}", exc_info=True)
        try:
            # Use send_error_message which requires the message object
            await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки стікера після батчинга.")
        except Exception as send_e:
            logger.error(f"Failed to send error message after batched sticker processing failure for user {user_telegram_id}: {send_e}")


@router.message(F.sticker)
async def sticker_handler(
    message: Message,
    bot: Bot,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO,
    user_dao: UserDAO,
    user: User, # User object should be available from middleware
    session_factory: async_sessionmaker[AsyncSession] # session_factory should be available from middleware
) -> None:
    """Handles incoming sticker messages"""
    chat = message.chat
    user_telegram_id = message.from_user.id
    chat_id = chat.id
    user_display_name = message.from_user.full_name or f"User {user_telegram_id}"

    logger.debug(f"Received sticker message {message.message_id} from user {user_display_name} (ID: {user_telegram_id}) in chat {chat_id}. Saving to DB.")

    # --- Preliminary Checks (Do these immediately) ---
    # User object should be guaranteed by middleware, but check global disable
    if user.is_global_disabled:
        logger.debug(f"Ignoring sticker message from user {user_telegram_id} due to global USER disable.")
        return

    # Get group from DB if this is a group chat
    group = await get_group_or_none(group_dao, chat)
    group_db_id = group.id if group else None

    # Check if the group has disabled the bot globally
    if group and group.is_global_disabled:
        logger.debug(f"Ignoring sticker message from user {user_telegram_id} in group {chat_id} due to global GROUP disable.")
        return

    # Check if the user has disabled sticker responses
    # Use getattr for backward compatibility in case the column doesn't exist yet
    if not getattr(user, 'responds_to_stickers', True):
        logger.debug(f"Ignoring sticker message from user {user_telegram_id} in chat {chat_id} due to USER sticker setting.")
        return

    # Check if the group has disabled sticker responses
    if group and not getattr(group, 'responds_to_stickers', True):
        logger.debug(f"Ignoring sticker message from user {user_telegram_id} in group chat {chat_id} due to GROUP sticker setting.")
        return

    sticker = message.sticker
    if not sticker:
        logger.error(f"Message {message.message_id} marked as sticker but no sticker object found.")
        await send_error_message(message, "Помилка: некоректні дані стікера.")
        return

    # --- Immediate Save to DB ---
    db_sticker = None # Initialize db_sticker to None
    try:
        # Формируем метаданные
        is_forwarded = bool(message.forward_from or message.forward_from_chat or message.forward_sender_name or message.forward_date)

        if is_forwarded:
            metadata = f"Message info: FORWARDED sticker shared by {user_display_name} (User ID: {user_telegram_id})"
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
            metadata = f"Message info: sticker from {user_display_name} (User ID: {user_telegram_id})"

        metadata += f", File ID: {sticker.file_id}, Set Name: {sticker.set_name or 'N/A'}, Emoji: {sticker.emoji or 'N/A'}, Message ID: {message.message_id}, Current time: {message.date}"

        # Download sticker file
        try:
            sticker_file_io = await bot.download(sticker.file_id, destination=io.BytesIO())
            if not sticker_file_io:
                 logger.error(f"Failed to download sticker file to BytesIO for message {message.message_id}")
                 await send_error_message(message, "Помилка: не вдалося завантажити файл стікера.")
                 return # Cannot proceed without file data
            sticker_file_io.seek(0) # Rewind to the beginning
            sticker_data = sticker_file_io.read()
            if not sticker_data:
                 logger.error(f"Downloaded sticker file data is empty for message {message.message_id}")
                 await send_error_message(message, "Помилка: завантажений файл стікера порожній.")
                 return # Cannot proceed with empty data
        except (TelegramNetworkError, TelegramBadRequest, TelegramForbiddenError) as e:
            logger.error(f"Telegram API error downloading sticker file for message {message.message_id}: {e}", exc_info=True)
            await send_error_message(message, "Помилка Телеграм API при завантаженні стікера.")
            return # Cannot proceed if download fails
        except Exception as e:
            logger.error(f"Unexpected error downloading sticker file for message {message.message_id}: {e}", exc_info=True)
            await send_error_message(message, "Неочікувана помилка при завантаженні стікера.")
            return # Cannot proceed if download fails


        # Get or create sticker in database
        async with session_factory() as session:
            sticker_dao = StickerDAO(session)

            # Process video sticker if needed BEFORE saving
            is_video = sticker.is_video
            if is_video:
                logger.info(f"Processing video sticker with duration: {sticker.duration}s")
                # Call the refactored processing function
                processed_video_data = process_video_data(sticker_data)
                db_sticker = await sticker_dao.get_or_create_sticker(
                    telegram_sticker_id=sticker.file_id,
                    telegram_message_id=message.message_id,
                    name=sticker.set_name,
                    emoji=sticker.emoji,
                    video_data=processed_video_data # Use processed data
                )
                metadata += f", Type: video sticker, Duration: {sticker.duration}s, Processed: {'Yes' if processed_video_data != sticker_data else 'No'}"
            else:
                 # For static stickers, just use the downloaded data
                db_sticker = await sticker_dao.get_or_create_sticker(
                    telegram_sticker_id=sticker.file_id,
                    telegram_message_id=message.message_id,
                    name=sticker.set_name,
                    emoji=sticker.emoji,
                    image_data=sticker_data # Use original data for static
                )
                metadata += ", Type: static sticker"

            await session.commit()
            # Access db_sticker.id after commit if needed elsewhere, though add_message doesn't strictly require commit first

        # Save the message to the database with sticker reference
        # This should happen regardless of processing success, as the original/processed data is saved
        await message_dao.add_message(
            user_id=user.id,
            role=MessageRole.USER,
            # Use emoji as text representation, fall back to Sticker description
            text=sticker.emoji or sticker.__class__.__name__, # Use __class__.__name__ as fallback for description like 'Sticker'
            group_id=group_db_id,
            telegram_message_id=message.message_id,
            message_metadata=metadata,
            sticker_id=db_sticker.id  # Add reference to the sticker
        )
        logger.debug(f"User sticker message {message.message_id} saved to DB (user {user_telegram_id}, group_id {group_db_id}, sticker_id {db_sticker.id}).")

    except Exception as e:
        logger.error(f"Failed to save user sticker message {message.message_id} to DB or process video: {e}", exc_info=True)
        await send_error_message(message, "Не вдалося зберегти ваш стікер або обробити відео.")
        # We cannot proceed to batching if the message wasn't saved correctly,
        # as the batching logic relies on retrieving message history.
        return

    # --- Pass to Batcher ---
    # This happens only if the sticker was successfully downloaded and saved to DB
    try:
        await message_batcher.handle_message(
            message=message,
            processing_callback=actual_sticker_processing_logic,
            session_factory=session_factory # Pass session_factory to the batcher callback
        )
        logger.debug(f"Sticker message {message.message_id} from user {user_telegram_id} passed to batcher.")
    except Exception as e:
        logger.error(f"Error passing sticker message {message.message_id} to batcher for user {user_telegram_id}: {e}", exc_info=True)
        await send_error_message(message, "Виникла проблема з системою обробки стікерів. Спробуйте знову.")