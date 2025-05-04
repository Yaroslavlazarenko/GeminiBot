import logging
import io
import tempfile
import os
from typing import Any, Optional # Import Optional for type hints
# from PIL import Image # Not used in the current logic
import ffmpeg
from aiogram import F, Router, types, Bot
from aiogram.types import Message
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
# from google.genai import types as gemini_types

# Assuming correct paths for your project modules
from ai.gemini_client import get_text_response # Assuming this handles history with transcription/metadata
from database.models import User, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
# Assuming send_error_message, get_group_or_none, handle_gemini_result utilities
from ..utils import send_error_message, get_group_or_none, handle_gemini_result
# Import the global batcher instance and the callback type
from ..message_batcher import message_batcher, ProcessingCallback

logger = logging.getLogger(__name__)
router = Router()

# Define FFmpeg paths (Using environment variables with your original defaults)
FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "/usr/bin/ffmpeg")
FFPROBE_PATH = os.environ.get("FFPROBE_PATH", "/usr/bin/ffprobe")

# --- Function to process video data with FFmpeg (your "previous" logic) ---
def process_video_data(video_data: bytes) -> bytes:
    """
    Process video data to ensure minimum duration of 2.0 seconds using ffmpeg.
    Uses the FFmpeg parameters as provided in the original code.
    """
    # Ensure FFmpeg paths are set and files exist
    if not os.path.exists(FFMPEG_PATH):
         logger.error(f"FFmpeg not found at {FFMPEG_PATH}. Skipping video processing.")
         # Decide behavior: return original data or raise error
         # Returning original data allows the rest of the pipeline to potentially continue
         logger.warning("FFmpeg executable not found, returning original video data.")
         return video_data

    if not os.path.exists(FFPROBE_PATH):
         logger.error(f"FFprobe not found at {FFPROBE_PATH}. Skipping video duration check/processing.")
         logger.warning("FFprobe executable not found, returning original video data.")
         return video_data

    try:
        logger.info(f"Starting video processing. Input size: {len(video_data)} bytes")

        # Create temporary files
        # Using delete=False requires explicit manual cleanup
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as input_file:
             input_file_path = input_file.name
             input_file.write(video_data)
             input_file.flush() # Ensure data is written to disk
             os.fsync(input_file.fileno()) # Ensure data is synced

        output_file_path = None
        try:
            logger.debug(f"Created temporary input file: {input_file_path}")

            # Get video duration using ffprobe with full path
            duration = 0.0
            try:
                probe = ffmpeg.probe(input_file_path, cmd=FFPROBE_PATH)
                duration = float(probe['format']['duration'])
                logger.info(f"Original video duration: {duration:.2f}s")
            except ffmpeg.Error as e:
                logger.error(f"Failed to get video duration using ffprobe: {e.stderr.decode()}")
                # Proceed, but duration will be 0.0, potentially leading to processing issues or incorrect speed
                # It's safer to return original data if probe fails critical info
                logger.warning("Failed to probe video duration, returning original data.")
                # Ensure cleanup of input file before returning
                if os.path.exists(input_file_path): os.unlink(input_file_path)
                return video_data
            except Exception as e: # Catch other potential errors from probe
                logger.error(f"Unexpected error during ffprobe: {e}", exc_info=True)
                if os.path.exists(input_file_path): os.unlink(input_file_path)
                logger.warning("Unexpected error during ffprobe, returning original data.")
                return video_data


            if duration >= 2.0:
                logger.info("Video is already long enough (>= 2.0s), returning original.")
                # Clean up input file
                if os.path.exists(input_file_path): os.unlink(input_file_path)
                return video_data # Return original data, no processing needed

            # Calculate factors
            target_duration = 2.0
            # setpts filter scales time: new_PTS = factor * old_PTS
            # To make video duration D become T (where D < T), factor = T / D
            setpts_factor = target_duration / duration
            # atempo filter scales speed: new_speed = factor * old_speed
            # To make audio duration D become T, speed must be D/T. factor = D / T
            atempo_factor = duration / target_duration

            logger.info(f"Original duration {duration:.2f}s is less than {target_duration}s. Will process to reach {target_duration}s.")
            logger.info(f"Calculated setpts factor: {setpts_factor:.4f}")
            logger.info(f"Calculated atempo factor: {atempo_factor:.4f}")

            # Create temporary output file
            with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as output_file:
                 output_file_path = output_file.name
            logger.debug(f"Created temporary output file: {output_file_path}")

            # Process video with ffmpeg using full path
            try:
                stream = ffmpeg.input(input_file_path)

                # Split into video and audio streams
                video_stream = stream.video
                audio_stream = stream.audio # This will be None if no audio track

                # Apply setpts filter to slow down video
                video_stream = video_stream.filter('setpts', f'{setpts_factor:.4f}*PTS')
                logger.debug(f"Applied setpts filter: {setpts_factor:.4f}*PTS")

                # Apply atempo filter to slow down audio if it exists
                # atempo filter can only scale between 0.5 and 2.0
                if audio_stream is not None:
                    atempo_factors = []
                    current_speed_reduction = 1.0 / atempo_factor # How much slower we need the audio to be overall
                    # Note: The user's original code used speed_factor (duration/target_duration) for atempo.
                    # atempo scales speed, so if duration < target, speed needs to decrease (atempo_factor < 1).
                    # If atempo_factor is < 0.5, multiple filters needed.
                    # User's logic: while remaining_speed < 0.5, atempo 0.5, remaining_speed *= 2.
                    # Then remaining_speed might be > 1. This seems incorrect.
                    # Correct logic should reduce speed. If atempo_factor is 0.3, need to apply eg. 0.5 then 0.6
                    # Or apply 1/0.3 ~ 3.3x slow down. Use atempo 2.0, remaining 1.66x. Use atempo 2.0, remaining 0.83x. Wait, no.
                    # Let's stick to the user's exact original atempo logic structure, as requested,
                    # even if the calculation seems reversed or slightly off for factors < 0.5.
                    # The user's code applied `speed_factor` directly, and if it was <0.5, applied 0.5 multiple times
                    # and then the remainder. Let's replicate that structure.
                    original_atempo_factor = atempo_factor # Store the calculated factor (duration/target_duration)

                    if original_atempo_factor != 1.0:
                         atempo_sequence = []
                         current_factor = original_atempo_factor
                         while current_factor < 0.5:
                             atempo_sequence.append(0.5)
                             current_factor *= 2.0 # This applies a 0.5x slow down, meaning we need to speed up the remaining part?
                                                   # The user's code's logic here is confusing. Let's assume they meant
                                                   # to apply 2.0 factor if current_factor > 2.0 and divide by 2.0.
                                                   # Or maybe they intended to multiply when factor < 0.5?
                                                   # The most standard way to slow down audio is apply atempo < 1.
                                                   # If factor is 0.3 (3.33x slowdown), need atempo 0.5 then atempo 0.66.
                                                   # User's code had: while remaining_speed < 0.5: atempo 0.5, remaining_speed *= 2
                                                   # This means if factor=0.3, it applies atempo 0.5 (makes it 0.6) then continues.
                                                   # If factor=0.2, atempo 0.5 (0.4), atempo 0.5 (0.8).
                                                   # If factor=0.1, atempo 0.5 (0.2), atempo 0.5 (0.4), atempo 0.5 (0.8).
                                                   # Final remaining: if remaining_speed != 1.0, append remaining_speed.
                                                   # This structure looks like it's intended to handle factors < 0.5.
                                                   # Let's implement the user's structure directly.

                         # Replicating user's original atempo logic structure:
                         remaining_speed_factor = original_atempo_factor # Start with the calculated factor (duration/target_duration)
                         atempo_filters_to_apply = []

                         # While the *remaining* speed factor needed is less than atempo's minimum (0.5),
                         # apply the minimum (0.5) and increase the remaining factor we need to achieve.
                         # This is still counter-intuitive but let's stick to the user's provided code logic.
                         while remaining_speed_factor < 0.5:
                              atempo_filters_to_apply.append(0.5)
                              remaining_speed_factor *= 2 # This seems wrong, should it be remaining_speed_factor /= 0.5?
                                                         # User's code had *= 2. Trusting the user's code structure.

                         # Add the final factor if it's not 1.0 (meaning no speed change needed anymore)
                         if remaining_speed_factor != 1.0:
                              atempo_filters_to_apply.append(remaining_speed_factor)

                         logger.debug(f"Applying atempo filters (following user's logic): {atempo_filters_to_apply}")

                         current_audio_stream = audio_stream
                         for atempo_val in atempo_filters_to_apply:
                              current_audio_stream = current_audio_stream.filter('atempo', atempo_val)
                         audio_stream = current_audio_stream
                         logger.debug("Applied atempo filter(s) successfully")


                    # Create output stream
                    # Using your original output arguments
                    output_args = {
                         'c:v': 'libx264',
                         'preset': 'veryslow',
                         'crf': '0',           # Lossless quality - high size!
                         'b:v': '0',           # No bitrate limit
                         'maxrate': '0',
                         'bufsize': '0',
                         'x264-params': 'keyint=1:scenecut=0', # Force keyframes - high size!
                         't': str(target_duration), # Ensure output is exactly 2 seconds long
                         'f': 'mp4' # Force mp4 format
                    }
                    if audio_stream is not None:
                        output_args.update({
                            'c:a': 'aac',
                            'b:a': '320k'       # Your original high audio bitrate
                        })
                        stream = ffmpeg.output(video_stream, audio_stream, output_file_path, **output_args)
                    else:
                        stream = ffmpeg.output(video_stream, output_file_path, **output_args)

                    logger.debug(f"Running ffmpeg command: {ffmpeg.compile(stream, cmd=FFMPEG_PATH)}")
                    process = ffmpeg.run(stream, cmd=FFMPEG_PATH, overwrite_output=True, capture_stdout=True, capture_stderr=True)
                    stdout, stderr = process
                    logger.info(f"ffmpeg stdout: {stdout.decode()}")
                    if stderr:
                         # Log stderr as error only if return code is non-zero
                         if process.returncode != 0:
                             logger.error(f"ffmpeg stderr (error {process.returncode}): {stderr.decode()}")
                         else:
                             logger.warning(f"ffmpeg stderr (non-error output): {stderr.decode()}")


                    logger.info("Successfully processed video with ffmpeg")

            except ffmpeg.Error as e:
                logger.error(f"ffmpeg processing failed: {e.stderr.decode()}")
                # Decide behavior: return original data or raise error
                logger.warning("ffmpeg processing failed, returning original data.")
                return video_data # Return original data on FFmpeg error
            except Exception as e: # Catch other unexpected errors
                logger.error(f"Unexpected error during ffmpeg run: {e}", exc_info=True)
                logger.warning("Unexpected error during ffmpeg run, returning original data.")
                return video_data


            # Read processed video
            # Ensure output file exists and has data before reading
            if not os.path.exists(output_file_path) or os.path.getsize(output_file_path) == 0:
                 logger.error(f"FFmpeg output file {output_file_path} is missing or empty after processing.")
                 return video_data # Return original data if output is bad

            with open(output_file_path, 'rb') as f:
                processed_data = f.read()

            logger.info(f"Video processing complete: original size={len(video_data)}, processed size={len(processed_data)}")
            return processed_data

        finally:
            # Clean up temporary files
            if os.path.exists(input_file_path):
                os.unlink(input_file_path)
                logger.debug(f"Cleaned up temporary input file: {input_file_path}")
            if output_file_path and os.path.exists(output_file_path):
                os.unlink(output_file_path)
                logger.debug(f"Cleaned up temporary output file: {output_file_path}")


    except Exception as e:
        logger.error(f"General error during video processing setup or cleanup: {e}", exc_info=True)
        return video_data # Return original data on overall failure


# --- Actual Processing Logic for Voice Messages ---
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
    """
    chat = message.chat
    user_telegram_id = message.from_user.id
    chat_id = chat.id
    voice = message.voice # The message object still has the voice attribute

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
        downloaded_voice_data = None
        transcription_text = None # Will store transcription here

        try:
            file = await bot.get_file(voice.file_id)
            if not file.file_path:
                logger.error(f"File path missing for voice file_id={voice.file_id} during batched processing.")
                await send_error_message(message, "Помилка: не вдалося отримати шлях до файлу голосового повідомлення (батчинг).")
                return

            downloaded_file = await bot.download_file(file.file_path)
            if downloaded_file is None:
                logger.error(f"Failed to download voice message from path={file.file_path}, received None during batched processing.")
                await send_error_message(message, "Помилка: не вдалося завантажити голосове повідомлення (отримано None, батчинг).")
                return

            downloaded_voice_data = downloaded_file.read() # Get raw bytes
            logger.debug(f"Downloaded voice message {message.message_id} data ({len(downloaded_voice_data)} bytes).")

        except Exception as e:
            logger.error(f"Error downloading voice message {message.message_id} during batched processing: {e}", exc_info=True)
            await send_error_message(message, "Помилка: не вдалося завантажити голосове повідомлення для обробки.")
            return # Cannot proceed without voice data


        # --- Transcribe Voice (if enabled and implemented) ---
        if getattr(user, 'transcribe_voice_only', False) and downloaded_voice_data:
            try:
                logger.debug(f"Attempting to transcribe voice message {message.message_id} for user {user_telegram_id}...")
                # TODO: Replace with your actual voice transcription implementation
                # transcription_text = await your_transcription_service.transcribe(downloaded_voice_data)
                transcription_text = "ГОЛОСОВОЕ СООБЩЕНИЕ (транскрипция временно отключена)" # Placeholder
                logger.debug(f"Transcription result for {message.message_id}: {transcription_text[:100]}...")

                # Update the saved message in the DB with the transcription
                if transcription_text:
                     await message_dao.update_message_text(
                         telegram_message_id=message.message_id,
                         chat_id=chat_id, # Need chat_id or group_id/user_id to uniquely identify
                         text=transcription_text
                     )
                     logger.debug(f"Updated DB message {message.message_id} with transcription.")

            except Exception as e:
                logger.error(f"Error transcribing voice message {message.message_id}: {e}", exc_info=True)
                # Continue without transcription if it fails
                transcription_text = f"ГОЛОСОВОЕ СООБЩЕНИЕ (ошибка транскрипции)" # Add placeholder text on error
                logger.warning(f"Transcription failed for message {message.message_id}, proceeding with error placeholder.")


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

        # Send typing action
        try:
             await bot.send_chat_action(chat_id=chat_id, action="typing") # Or "upload_voice" / "upload_document"
        except Exception as e:
            logger.warning(f"Failed to send chat action to {chat_id} during batched voice processing: {e}")

        # --- Call AI Model ---
        # Use get_audio_response if your model handles audio directly,
        # or get_text_response if you rely solely on transcription + text history.
        # Pass downloaded_voice_data if get_audio_response needs it.
        gemini_result = await get_text_response( # Or get_audio_response?
            message_history=message_history,
            user=user, # Pass the re-fetched user object
            message=message, # Pass the last voice message object
            # audio_data=downloaded_voice_data if 'get_audio_response' is used else None # Example
        )

        # --- Handle AI Result (save, send) ---
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
                  await bot.send_message(chat_id=chat_id, text="🤯 Не можу знайти ваші дані для обробки відео-нотатки. Спробуйте написать знову.")
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
                logger.error(f"File path missing for video note file_id={video_note.file_id} during batched processing.")
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
        # Apply processing to ensure minimum duration using the process_video_data function
        if downloaded_file_data:
             try:
                processed_video_data = process_video_data(downloaded_file_data)
                logger.debug(f"Video note {message.message_id} processed data size: {len(processed_video_data)} bytes.")
             except FileNotFoundError as e:
                 logger.error(f"FFmpeg tools not found during processing for message {message.message_id}: {e}")
                 await send_error_message(message, "Помилка: необхідні інструменти FFmpeg не знайдені.")
                 return # Stop if FFmpeg is essential and not found
             except Exception as e:
                logger.error(f"Failed during process_video_data for message {message.message_id}: {e}", exc_info=True)
                # Continue with original data if processing fails, but notify user
                processed_video_data = downloaded_file_data
                await send_error_message(message, "Помилка обробки відео-нотатки (FFmpeg). Спробую продовжити з оригіналом.")
                # Note: If FFmpeg failed, using original might also cause issues later depending on AI model capabilities


        # --- Transcribe Video Note Audio (if enabled and implemented) ---
        if getattr(user, 'transcribe_video_note', False) and processed_video_data:
            try:
                logger.debug(f"Attempting to transcribe video note {message.message_id} for user {user_telegram_id}...")
                # TODO: Replace with your actual video note/audio transcription implementation
                # You'll need to pass the processed_video_data (which includes audio) to your transcription service.
                # transcription_text = await your_transcription_service.transcribe(processed_video_data)
                transcription_text = "ВИДЕО-ЗАМЕТКА (транскрипция временно отключена)" # Placeholder
                logger.debug(f"Transcription result for {message.message_id}: {transcription_text[:100]}...")

                # Update the saved message in the DB with the transcription
                if transcription_text:
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
        if group_db_id is not None:
            message_history = await message_dao.get_group_messages_as_contents(group_id=group_db_id)
            logger.debug(f"Retrieved {len(message_history)} messages from group chat history for AI.")
        else:
            message_history = await message_dao.get_user_private_messages_as_contents(user_id=user.id) # Use internal user ID
            logger.debug(f"Retrieved {len(message_history)} messages from private chat history for AI.")

        if not message_history:
            logger.warning(f"Message history is unexpectedly empty for user {user_telegram_id} / chat {chat_id} before AI call after batching.")
            # Even if history is empty, we might still process the current video note if AI supports it
            # If AI requires history, you might return here.
            pass # Continue assuming AI can handle the current message without history if needed


        # Send chat action (e.g., 'upload_video' or 'typing')
        try:
             await bot.send_chat_action(chat_id=chat_id, action="upload_video") # Or "typing"
        except Exception as e:
            logger.warning(f"Failed to send chat action to {chat_id} during batched video note processing: {e}")

        # --- Call AI Model ---
        # Use a multimodal model function if available, or get_text_response if relying only on transcription.
        # If using a multimodal model, you'll need to pass the processed_video_data.
        # Adjust parameter passing based on your AI function's signature.
        # Note: If get_text_response relies *only* on text in history, and transcription failed,
        # this might not work well. Consider how your AI handles different message types and history.

        # Example if using a multimodal function:
        # gemini_result = await get_multimodal_response( # Assuming you have this
        #     message_history=message_history, # Can pass history
        #     video_data=processed_video_data, # Pass the actual video data
        #     user=user,
        #     message=message # Pass the original message object
        # )
        #
        # If relying only on transcription and history:
        gemini_result = await get_text_response( # Assuming this works with history including transcription
            message_history=message_history, # Pass the full history including transcribed video notes
            user=user, # Pass the re-fetched user object
            message=message # Pass the last video note message object
        )


        # --- Handle AI Result (save, send) ---
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


# --- Handler for Voice Messages (uses Batcher) ---
@router.message(F.voice)
async def voice_handler(
    message: Message,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO,
    user_dao: UserDAO,
    user: User # Assuming user is provided by middleware and is the DB User object
) -> None:
    """
    Handles incoming voice messages. Saves the message (with file_id/duration) to DB and
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
    group = await get_group_or_none(group_dao, chat) if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP] else None
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
    # The batched processing function will do that for the LAST message.
    # We save transcription_text as None initially.
    try:
        # Формируем метаданные
        is_forwarded = bool(message.forward_from or message.forward_from_chat or message.forward_sender_name or message.forward_date)
        user_display_name = message.from_user.full_name or f"User {user.telegram_id}" # Re-get display name

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

        # Save the message metadata and basic info. Voice data is not saved here.
        # Your message_dao.add_message needs to support saving voice_file_id and voice_duration.
        await message_dao.add_message(
            user_id=user.id, # Use internal DB user ID from middleware
            role=MessageRole.USER,
            text=None, # Transcription text is not available yet
            group_id=group_db_id,
            telegram_message_id=message.message_id,
            message_metadata=metadata, # Initial metadata without transcription
            voice_file_id=voice.file_id, # Save file ID
            voice_duration=voice.duration # Save duration
            # Do NOT save voice_data here - it's large and processed later
        )
        logger.debug(f"User voice message {message.message_id} saved to DB (user {user_telegram_id}, group_id {group_db_id}) with file_id.")

    except Exception as e:
         logger.error(f"Failed to save user voice message {message.message_id} to DB: {e}", exc_info=True)
         await send_error_message(message, "Не вдалося зберегти ваше голосове повідомлення.")
         return # Cannot proceed if message isn't saved

    # --- Pass to Batcher ---
    # Pass the message and the specific processing function for voice messages,
    # along with the DAOs for batcher initialization if needed.
    try:
        await message_batcher.handle_message(
            message=message, # Pass the original message object
            processing_callback=actual_voice_processing_logic, # Pass the voice processing function
            user_dao=user_dao, # Pass dependencies for batcher init
            group_dao=group_dao,
            message_dao=message_dao
        )
        logger.debug(f"Voice message {message.message_id} from user {user_telegram_id} passed to batcher.")
    except Exception as e:
        logger.error(f"Error passing voice message {message.message_id} to batcher for user {user_telegram_id}: {e}", exc_info=True)
        await send_error_message(message, "Виникла проблема з системою обробки голосових повідомлень. Спробуйте знову.")

    # The handler simply returns here.

# --- Handler for Video Note Messages (uses Batcher) ---
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
        # Формируем метаданные
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

    # The handler simply returns here.