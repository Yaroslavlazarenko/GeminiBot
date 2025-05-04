import logging
import io
import tempfile
import os
from typing import Any # Import Any for type hints for AI response handling if needed
# from PIL import Image # Not used in the current video_note logic, remove if not needed elsewhere
import ffmpeg
from aiogram import F, Router, types, Bot # Import Bot for type hint
from aiogram.types import Message
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
# from google.genai import types as gemini_types # Not used directly here, keep if needed elsewhere

# Assuming correct paths for your project modules
from ai.gemini_client import get_text_response # Assuming this handles history with transcription
from database.models import User, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
# Assuming send_error_message, get_group_or_none, handle_gemini_result utilities
from ..utils import send_error_message, get_group_or_none, handle_gemini_result
# Import the global batcher instance and the callback type
from ..message_batcher import message_batcher, ProcessingCallback

logger = logging.getLogger(__name__)
router = Router()

# Define FFmpeg paths (keep these, they are used in process_video_data)
FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "/usr/bin/ffmpeg") # Use environment variables for path
FFPROBE_PATH = os.environ.get("FFPROBE_PATH", "/usr/bin/ffprobe")

def process_video_data(video_data: bytes) -> bytes:
    """
    Process video data to ensure minimum duration of 2.0 seconds using ffmpeg.
    This function is called within the batched processing logic.
    """
    # Ensure FFmpeg paths are set
    if not os.path.exists(FFMPEG_PATH) or not os.path.exists(FFPROBE_PATH):
         logger.error(f"FFmpeg or FFprobe not found at {FFMPEG_PATH} or {FFPROBE_PATH}. Skipping video processing.")
         # Raise an error or return original data based on desired behavior
         raise FileNotFoundError(f"FFmpeg tools not found. Check paths: {FFMPEG_PATH}, {FFPROBE_PATH}")


    try:
        logger.info(f"Starting video processing. Input size: {len(video_data)} bytes")

        # Use io.BytesIO for input/output streams directly with ffmpeg-python
        # This avoids writing to disk for input, but might still need temp file for output
        # depending on ffmpeg-python capabilities or complex filters.
        # Let's stick to temp files for robustness with complex processing.

        # Create temporary files
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as input_file, \
             tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as output_file:

            try:
                # Write input video data
                input_file.write(video_data)
                input_file.flush() # Ensure data is written to disk
                os.fsync(input_file.fileno()) # Ensure data is synced to disk
                logger.debug(f"Wrote {len(video_data)} bytes to temporary input file: {input_file.name}")

                # Get video duration using ffprobe with full path
                try:
                    probe = ffmpeg.probe(input_file.name, cmd=FFPROBE_PATH)
                    duration = float(probe['format']['duration'])
                    logger.info(f"Original video duration: {duration:.2f}s")
                except ffmpeg.Error as e:
                    logger.error(f"Failed to get video duration using ffprobe: {e.stderr.decode()}")
                    # Clean up temp file before returning/raising
                    os.unlink(input_file.name)
                    # Decide behavior: return original data or raise error
                    logger.warning("Failed to probe video duration, returning original data.")
                    return video_data
                except Exception as e: # Catch other potential errors from probe
                    logger.error(f"Unexpected error during ffprobe: {e}", exc_info=True)
                    os.unlink(input_file.name)
                    logger.warning("Unexpected error during ffprobe, returning original data.")
                    return video_data


                if duration >= 2.0:
                    logger.info("Video is already long enough (>= 2.0s), returning original.")
                    os.unlink(input_file.name) # Clean up input file
                    return video_data # Return original data, no processing needed

                # Calculate speed factor to reach 2.0 seconds
                target_duration = 2.0
                # Speed factor is how much FASTER it needs to play to cover original duration in target_duration
                # i.e., if duration is 1s, target is 2s, speed_factor = 1s / 2s = 0.5. Slow down by 0.5x
                # setpts = 1 / speed_factor * PTS = 1 / 0.5 * PTS = 2 * PTS (makes video twice as slow)
                # atempo = speed_factor (makes audio twice as slow if < 1)
                speed_factor = duration / target_duration
                logger.info(f"Original duration {duration:.2f}s is less than {target_duration}s. Will process to reach {target_duration}s.")
                logger.info(f"Calculated speed factor: {speed_factor:.4f} (target_duration / original_duration = {target_duration / duration:.4f})")
                # The speed factor applied to setpts should be the reciprocal
                setpts_factor = target_duration / duration
                logger.info(f"setpts filter factor: {setpts_factor:.4f}")
                logger.info(f"atempo filter factor: {speed_factor:.4f}")


                # Process video with ffmpeg using full path
                try:
                    stream = ffmpeg.input(input_file.name)

                    # Split into video and audio streams
                    video_stream = stream.video
                    audio_stream = stream.audio # This will be None if no audio track

                    # Apply setpts filter to slow down video
                    # setpts='(target_duration / original_duration) * PTS'
                    video_stream = video_stream.filter('setpts', f'{setpts_factor:.4f}*PTS')
                    logger.debug(f"Applied setpts filter: {setpts_factor:.4f}*PTS")


                    # Apply atempo filter to slow down audio if it exists
                    # atempo filter can only scale between 0.5 and 2.0
                    if audio_stream is not None:
                        atempo_factors = []
                        current_speed_reduction = 1.0 / speed_factor # How much slower we need the audio to be overall
                        logger.debug(f"Required audio speed reduction (atempo total factor): {current_speed_reduction:.4f}")

                        # If current_speed_reduction is > 2.0, apply multiple atempo filters
                        while current_speed_reduction > 2.0:
                            atempo_factors.append(2.0)
                            current_speed_reduction /= 2.0
                        # Apply the remaining factor (will be <= 2.0)
                        atempo_factors.append(current_speed_reduction)

                        logger.debug(f"Applying atempo filters: {atempo_factors}")

                        # Apply atempo filters sequentially
                        current_audio_stream = audio_stream
                        for atempo_val in atempo_factors:
                            current_audio_stream = current_audio_stream.filter('atempo', atempo_val)
                        audio_stream = current_audio_stream
                        logger.debug("Applied atempo filter(s) successfully")


                    # Create output stream
                    output_args = {
                         'c:v': 'libx264',      # Use h264 codec
                         'preset': 'veryslow',  # Highest quality preset for small files
                         'crf': '0',           # Lossless quality (large size!) - Consider changing this
                         'b:v': '0',           # No bitrate limit
                         'maxrate': '0',
                         'bufsize': '0',
                         'x264-params': 'keyint=1:scenecut=0', # Force keyframes - might increase size
                         't': str(target_duration), # Ensure output is exactly 2 seconds long
                         'f': 'mp4' # Force mp4 format
                    }
                    if audio_stream is not None:
                        output_args.update({
                            'c:a': 'aac',       # Use aac audio codec
                            'b:a': '128k'       # Reasonable audio bitrate (320k might be overkill)
                        })
                        stream = ffmpeg.output(video_stream, audio_stream, output_file.name, **output_args)
                    else:
                        stream = ffmpeg.output(video_stream, output_file.name, **output_args)

                    logger.debug(f"Running ffmpeg command: {ffmpeg.compile(stream, cmd=FFMPEG_PATH)}")
                    process = ffmpeg.run(stream, cmd=FFMPEG_PATH, overwrite_output=True, capture_stdout=True, capture_stderr=True)
                    stdout, stderr = process
                    logger.info(f"ffmpeg stdout: {stdout.decode()}")
                    if stderr:
                         logger.warning(f"ffmpeg stderr: {stderr.decode()}")

                    logger.info("Successfully processed video with ffmpeg")

                except ffmpeg.Error as e:
                    logger.error(f"ffmpeg processing failed: {e.stderr.decode()}")
                    # Clean up temp files on failure
                    os.unlink(input_file.name)
                    if os.path.exists(output_file.name): os.unlink(output_file.name) # Output might not exist
                    # Decide behavior: return original data or raise error
                    logger.warning("ffmpeg processing failed, returning original data.")
                    return video_data # Return original data on FFmpeg error
                except Exception as e: # Catch other unexpected errors
                    logger.error(f"Unexpected error during ffmpeg run: {e}", exc_info=True)
                    os.unlink(input_file.name)
                    if os.path.exists(output_file.name): os.unlink(output_file.name)
                    logger.warning("Unexpected error during ffmpeg run, returning original data.")
                    return video_data


                # Read processed video
                # Ensure output file exists and has data before reading
                if not os.path.exists(output_file.name) or os.path.getsize(output_file.name) == 0:
                     logger.error(f"FFmpeg output file {output_file.name} is missing or empty.")
                     os.unlink(input_file.name)
                     # Output might not exist, check again before unlink
                     if os.path.exists(output_file.name): os.unlink(output_file.name)
                     return video_data # Return original data if output is bad

                with open(output_file.name, 'rb') as f:
                    processed_data = f.read()

                # Clean up
                os.unlink(input_file.name)
                os.unlink(output_file.name)

                logger.info(f"Video processing complete: original size={len(video_data)}, processed size={len(processed_data)}")
                return processed_data

            except Exception as inner_e:
                # Catch errors within the temp file context
                logger.error(f"Error within temporary file context: {inner_e}", exc_info=True)
                # Attempt cleanup if files exist
                if os.path.exists(input_file.name): os.unlink(input_file.name)
                if os.path.exists(output_file.name): os.unlink(output_file.name)
                return video_data # Return original data on failure

    except Exception as e:
        logger.error(f"General error during video processing setup: {e}", exc_info=True)
        return video_data # Return original data on overall failure


# --- Actual Processing Logic for Video Note Messages ---
# This function is called by the MessageBatcher when the quiet period is met.
async def actual_video_note_processing_logic(
    bot: Bot,
    message: Message,
    user_dao: UserDAO,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO,
) -> None:
    """
    Performs the actual processing logic for a video note message after batching.
    Downloads, processes with FFmpeg, transcribes, fetches history, calls AI, saves response, sends.
    It assumes the incoming message has already been saved to the DB with file_id and duration.
    """
    chat = message.chat
    user_telegram_id = message.from_user.id
    chat_id = chat.id
    video_note = message.video_note # The message object still has the video_note attribute

    logger.info(f"Starting batched video note processing for user {user_telegram_id} in chat {chat_id} (last message ID: {message.message_id})")

    try:
        # Re-fetch User and Group objects to ensure we have the latest settings
        user = await user_dao.get_user_by_telegram_id(user_telegram_id)
        if not user:
             logger.error(f"User {user_telegram_id} not found in DB during batched video note processing. Cannot proceed.")
             try:
                  await bot.send_message(chat_id=chat_id, text="🤯 Не можу знайти ваші дані для обробки відео-нотатки. Спробуйте написати знову.")
             except Exception as send_e:
                  logger.error(f"Failed to send user data error message to {chat_id}: {send_e}")
             return # Stop processing

        group = await get_group_or_none(group_dao, chat)
        group_db_id = group.id if group else None

        # Check global/video note response settings again (could have changed)
        if user.is_global_disabled or not getattr(user, 'responds_to_video_note', True):
            logger.debug(f"Ignoring batched video note processing for user {user_telegram_id} due to updated user settings.")
            return

        if group and (group.is_global_disabled or not getattr(group, 'responds_to_video_note', True)):
             logger.debug(f"Ignoring batched video note processing for user {user_telegram_id} in group {chat_id} due to updated group settings.")
             return

        # --- Download Video Note File ---
        # This happens NOW, inside the batched processing
        downloaded_file_data = None
        processed_video_data = None # Data after FFmpeg processing
        transcription_text = None # Will store transcription here

        try:
            file = await bot.get_file(video_note.file_id)
            if not file.file_path:
                logger.error(f"File path is missing for video note file_id={video_note.file_id} during batched processing.")
                await send_error_message(message, "Помилка: не вдалося отримати шлях до файлу відео-нотатки (батчинг).")
                return

            downloaded_file = await bot.download_file(file.file_path)
            if downloaded_file is None:
                logger.error(f"Failed to download video note from path={file.file_path}, received None during batched processing.")
                await send_error_message(message, "Помилка: не вдалося завантажити відео-нотатку (отримано None, батчинг).")
                return

            downloaded_file_data = downloaded_file.read()
            logger.debug(f"Downloaded video note {message.message_id} data ({len(downloaded_file_data)} bytes).")

        except Exception as e:
            logger.error(f"Error downloading video note message {message.message_id} during batched processing: {e}", exc_info=True)
            await send_error_message(message, "Помилка: не вдалося завантажити відео-нотатку для обробки.")
            return # Cannot proceed without video data


        # --- Process Video Note (FFmpeg) ---
        # Apply processing to ensure minimum duration
        if downloaded_file_data:
             try:
                processed_video_data = process_video_data(downloaded_file_data)
                logger.debug(f"Video note {message.message_id} processed data size: {len(processed_video_data)} bytes.")
             except Exception as e:
                logger.error(f"Failed during process_video_data for message {message.message_id}: {e}", exc_info=True)
                # Decide behavior: stop or continue with original data
                # Let's continue with original data if processing fails
                processed_video_data = downloaded_file_data
                await send_error_message(message, "Помилка обробки відео-нотатки (FFmpeg). Спробую продовжити з оригіналом.")


        # --- Transcribe Video Note Audio (if enabled and implemented) ---
        # This happens NOW
        if getattr(user, 'transcribe_video_note', False) and processed_video_data:
            try:
                logger.debug(f"Attempting to transcribe video note {message.message_id} for user {user_telegram_id}...")
                # TODO: Replace with your actual video note/audio transcription implementation
                # You'll need to pass the processed_video_data (which includes audio) to your transcription service.
                # transcription_text = await your_transcription_service.transcribe(processed_video_data)
                transcription_text = "ВИДЕО-ЗАМЕТКА (транскрипция временно отключена)" # Placeholder
                logger.debug(f"Transcription result for {message.message_id}: {transcription_text[:100]}...")

                # Optionally, update the saved message in the DB with the transcription
                # so that future history fetches include it.
                if transcription_text:
                     # Need to update the DB record for this message_id
                     await message_dao.update_message_text(
                         telegram_message_id=message.message_id,
                         chat_id=chat_id, # Or use message.chat.id to be safe
                         text=transcription_text
                     )
                     logger.debug(f"Updated DB message {message.message_id} with transcription.")

            except Exception as e:
                logger.error(f"Error transcribing video note {message.message_id}: {e}", exc_info=True)
                transcription_text = f"ВИДЕО-ЗАМЕТКА (ошибка транскрипции)" # Add placeholder text on error
                logger.warning(f"Transcription failed for message {message.message_id}, proceeding with error placeholder.")


        # --- Retrieve Message History ---
        # Get the full history *after* potentially updating the latest message with transcription
        # The history should include the transcription text for the video note.
        if group_db_id is not None:
            message_history = await message_dao.get_group_messages_as_contents(group_id=group_db_id)
            logger.debug(f"Retrieved {len(message_history)} messages from group chat history for AI.")
        else:
            message_history = await message_dao.get_user_private_messages_as_contents(user_id=user.id) # Use internal user ID
            logger.debug(f"Retrieved {len(message_history)} messages from private chat history for AI.")

        if not message_history:
            logger.warning(f"Message history is unexpectedly empty for user {user_telegram_id} / chat {chat_id} before AI call after batching.")
            return # Nothing to process

        # Send chat action (e.g., 'upload_video' or 'typing')
        try:
             await bot.send_chat_action(chat_id=chat_id, action="upload_video") # Or "typing"
        except Exception as e:
            logger.warning(f"Failed to send chat action to {chat_id} during batched video note processing: {e}")

        # --- Call AI Model ---
        # Use a multimodal model function if available, or get_text_response if relying only on transcription.
        # If using a multimodal model, you'll need to pass the processed_video_data.
        # Adjust parameter passing based on your AI function's signature.
        # Example if using a multimodal function:
        # gemini_result = await get_multimodal_response( # Assuming you have this
        #     message_history=message_history,
        #     video_data=processed_video_data,
        #     user=user,
        #     message=message
        # )
        #
        # If relying only on transcription:
        gemini_result = await get_text_response( # Assuming this works with history including transcription
            message_history=message_history,
            user=user, # Pass the re-fetched user object
            message=message # Pass the last video note message object
        )


        # --- Handle AI Result (save, send) ---
        # Assuming handle_gemini_result is generic enough or you have a handle_multimodal_result
        await handle_gemini_result( # Or handle_multimodal_result?
            gemini_result, # The result from your AI call
            message, # Pass the last message object (the video note message)
            message_dao=message_dao, # Pass DAOs
            user_dao=user_dao,
            user=user, # Pass the re-fetched user object
            group_db_id=group_db_id # Pass group ID
        )

        logger.info(f"Successfully processed batched video note for user {user_telegram_id} in chat {chat_id}")

    except Exception as e:
        logger.error(f"Error in batched video note processing logic for user {user_telegram_id} in chat {chat_id} (last message ID: {message.message_id}): {e}", exc_info=True)
        # Use the bot instance passed to this function to send an error message
        try:
            await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки відео-нотатки після батчинга.")
        except Exception as send_e:
             logger.error(f"Failed to send error message after batched video note processing failure for user {user_telegram_id}: {send_e}")


# --- Handler that uses the Batcher ---
@router.message(F.video_note)
async def video_note_handler(
    message: Message,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO,
    user_dao: UserDAO,
    user: User # Assuming user is provided by middleware and is the DB User object
) -> None:
    """
    Handles incoming video note messages. Saves the message (with file_id/duration)
    to DB and passes it to the message batcher for timed processing.
    """
    chat = message.chat
    user_display_name = message.from_user.full_name or f"User {user.telegram_id}"
    chat_id = chat.id
    user_telegram_id = user.telegram_id

    logger.debug(f"Received video note message {message.message_id} from user {user_display_name} (ID: {user_telegram_id}) in chat {chat_id}. Saving to DB.")

    # --- Preliminary Checks (Do these immediately) ---
    if user.is_global_disabled:
        logger.debug(f"Ignoring video note message from user {user_telegram_id} due to global USER disable.")
        return
    group = await get_group_or_none(group_dao, chat) if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP] else None
    group_db_id = group.id if group else None # Need group_db_id for saving

    if group and group.is_global_disabled:
        logger.debug(f"Ignoring video note message from user {user_telegram_id} in group {chat_id} due to global GROUP disable.")
        return

    # Check video note specific settings - if disabled, no need to even save or batch
    if not getattr(user, 'responds_to_video_note', True):
        logger.debug(f"Ignoring video note message from user {user_telegram_id} in chat {chat_id} due to USER video note setting.")
        return
    if group and not getattr(group, 'responds_to_video_note', True):
        logger.debug(f"Ignoring video note message from user {user_telegram_id} in group chat {chat_id} due to GROUP video note setting.")
        return

    video_note = message.video_note
    if not video_note:
        logger.error(f"Message {message.message_id} marked as video note but no video note object found.")
        await send_error_message(message, "Помилка: некоректні дані відео-нотатки.")
        return

    # --- Immediate Save to DB ---
    # We save the message immediately including file_id and duration.
    # We DO NOT download the video data, process with FFmpeg, or transcribe here.
    # The batched processing function will do that for the LAST message.
    # We save transcription_text (text field) as None initially.
    try:
        # Формируем метаданные для видео-заметки
        is_forwarded = bool(message.forward_from or message.forward_from_chat or message.forward_sender_name or message.forward_date)
        user_display_name = message.from_user.full_name or f"User {user.telegram_id}" # Re-get display name

        if is_forwarded:
            metadata = f"Message info: FORWARDED video note shared by {user_display_name} (User ID: {user.telegram_id})"
            # Add detailed forwarding information as before
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
            metadata = f"Message info: video note from {user_display_name} (User ID: {user.telegram_id})"

        metadata += f", Duration: {video_note.duration}s, File ID: {video_note.file_id}, Message ID: {message.message_id}, Current time: {message.date}"
        # Note: transcription_text is added to metadata *after* transcription in the processing logic.

        # Save the message metadata and basic info. Video data is not saved here.
        # Your message_dao.add_message needs to support saving video_note_file_id and video_note_duration.
        await message_dao.add_message(
            user_id=user.id, # Use internal DB user ID from middleware
            role=MessageRole.USER,
            text=None, # Transcription text is not available yet
            group_id=group_db_id,
            telegram_message_id=message.message_id,
            message_metadata=metadata, # Initial metadata without transcription
            video_note_file_id=video_note.file_id, # Save file ID
            video_note_duration=video_note.duration # Save duration
            # Do NOT save video_data here
        )
        logger.debug(f"User video note message {message.message_id} saved to DB (user {user_telegram_id}, group_id {group_db_id}) with file_id.")

    except Exception as e:
         logger.error(f"Failed to save user video note message {message.message_id} to DB: {e}", exc_info=True)
         # If saving fails, we cannot reliably process this message later.
         await send_error_message(message, "Не вдалося зберегти вашу відео-нотатку.")
         return # Cannot proceed if message isn't saved

    # --- Pass to Batcher ---
    # Pass the message and the specific processing function for video note messages,
    # along with the DAOs for batcher initialization if needed.
    try:
        await message_batcher.handle_message(
            message=message, # Pass the original message object
            processing_callback=actual_video_note_processing_logic, # Pass the video note processing function
            user_dao=user_dao, # Pass dependencies for batcher init
            group_dao=group_dao,
            message_dao=message_dao
        )
        logger.debug(f"Video note message {message.message_id} from user {user_telegram_id} passed to batcher.")
    except Exception as e:
        # If batcher fails (e.g., internal error, couldn't start timer), processing won't happen.
        logger.error(f"Error passing video note message {message.message_id} to batcher for user {user_telegram_id}: {e}", exc_info=True)
        await send_error_message(message, "Виникла проблема з системою обробки відео-нотаток. Спробуйте знову.")

    # The handler simply returns here. The actual_video_note_processing_logic will be triggered
    # asynchronously by the batcher's timer if the quiet period is met for this user.