import logging
import io
import tempfile
import os
from PIL import Image
import ffmpeg
from aiogram import F, Router, types
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from google.genai import types as gemini_types

from ai.gemini_client import get_video_response
from database.models import User, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
from ..utils import send_error_message, get_group_or_none, handle_gemini_result

logger = logging.getLogger(__name__)
router = Router()

# Define FFmpeg paths
FFMPEG_PATH = "/usr/bin/ffmpeg"
FFPROBE_PATH = "/usr/bin/ffprobe"

def process_video_data(video_data: bytes) -> bytes:
    """Process video data to ensure minimum duration of 2.0 seconds using ffmpeg."""
    try:
        logger.info(f"Starting video processing. Input size: {len(video_data)} bytes")
        
        # Create temporary files
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as input_file, \
             tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as output_file:
            
            # Write input video data
            input_file.write(video_data)
            input_file.flush()
            logger.info(f"Created temporary input file: {input_file.name}")
            
            # Get video duration using ffprobe with full path
            try:
                probe = ffmpeg.probe(input_file.name, cmd=FFPROBE_PATH)
                duration = float(probe['format']['duration'])
                logger.info(f"Original video duration: {duration:.2f}s")
            except ffmpeg.Error as e:
                logger.error(f"Failed to get video duration: {e.stderr.decode()}")
                return video_data
            
            if duration >= 2.0:
                logger.info("Video is already long enough, returning original")
                os.unlink(input_file.name)
                return video_data
            
            # Calculate speed factor to reach 2.0 seconds
            target_duration = 2.0
            speed_factor = duration / target_duration
            logger.info(f"Will slow down video by factor {speed_factor:.2f} to reach {target_duration}s")
            
            # Process video with ffmpeg using full path
            try:
                stream = ffmpeg.input(input_file.name)
                
                # Split into video and audio streams
                video = stream.video
                audio = stream.audio
                
                # Apply setpts filter to slow down video
                video = video.filter('setpts', f'{1/speed_factor}*PTS')
                
                # Apply atempo filter to slow down audio if it exists
                if audio is not None:
                    # Calculate how many atempo filters we need
                    atempo_filters = []
                    remaining_speed = speed_factor
                    
                    # If speed factor is less than 0.5, we need multiple atempo filters
                    while remaining_speed < 0.5:
                        atempo_filters.append(0.5)  # Each atempo filter can slow down by 0.5x
                        remaining_speed *= 2
                    
                    # Add the final atempo filter if needed
                    if remaining_speed != 1.0:
                        atempo_filters.append(remaining_speed)
                    
                    # Apply atempo filters sequentially
                    if atempo_filters:
                        audio = audio.filter('atempo', atempo_filters[0])
                        for atempo in atempo_filters[1:]:
                            audio = audio.filter('atempo', atempo)
                
                # Create output stream
                if audio is not None:
                    stream = ffmpeg.output(video, audio, output_file.name)
                else:
                    stream = ffmpeg.output(video, output_file.name)
                
                # Set output parameters to maintain file size
                stream = stream.overwrite_output()
                stream = stream.global_args('-t', str(target_duration))
                stream = stream.global_args('-c:v', 'libx264')  # Use h264 codec
                stream = stream.global_args('-preset', 'veryslow')  # Highest quality preset
                stream = stream.global_args('-crf', '0')  # Lossless quality
                stream = stream.global_args('-b:v', '0')  # No bitrate limit
                stream = stream.global_args('-maxrate', '0')  # No maximum bitrate
                stream = stream.global_args('-bufsize', '0')  # No buffer size limit
                stream = stream.global_args('-x264-params', 'keyint=1:scenecut=0')  # Force keyframes every frame
                
                if audio is not None:
                    stream = stream.global_args('-c:a', 'aac')
                    stream = stream.global_args('-b:a', '320k')  # High audio bitrate
                
                ffmpeg.run(stream, cmd=FFMPEG_PATH, overwrite_output=True, capture_stdout=True, capture_stderr=True)
                logger.info("Successfully processed video with ffmpeg")
                
            except ffmpeg.Error as e:
                logger.error(f"ffmpeg processing failed: {e.stderr.decode()}")
                os.unlink(input_file.name)
                return video_data
            
            # Read processed video
            with open(output_file.name, 'rb') as f:
                processed_data = f.read()
            
            # Clean up
            os.unlink(input_file.name)
            os.unlink(output_file.name)
            
            logger.info(f"Video processing complete: original size={len(video_data)}, processed size={len(processed_data)}")
            return processed_data
            
    except Exception as e:
        logger.error(f"Error processing video: {e}", exc_info=True)
        return video_data

@router.message(F.video_note)
async def video_note_handler(
    message: Message,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO,
    user_dao: UserDAO,
    user: User
) -> None:
    """Обрабатывает видео-кружки (video notes), проверяя настройки пользователя и группы."""
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
            logger.debug(f"Processing forwarded video note from original sender {original_sender.full_name} (ID: {original_sender.id})")
        else:
            logger.debug(f"Processing forwarded video note from unknown original sender")

    # Check global response setting first
    if user.is_global_disabled:
        logger.debug(f"Ignoring video note from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id} due to global USER disable.")
        return
    if group and group.is_global_disabled:
        logger.debug(f"Ignoring video note from user {user_display_name} (ID: {user.telegram_id}) in group chat {chat.id} due to global GROUP disable.")
        return

    # Then check video note-specific setting
    if not user.responds_to_video_note:
        logger.debug(f"Ignoring video note from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id} due to USER video note setting.")
        return
    if group and not getattr(group, 'responds_to_video_note', True):
        logger.debug(f"Ignoring video note from user {user_display_name} (ID: {user.telegram_id}) in group chat {chat.id} due to GROUP video note setting.")
        return

    if not message.video_note:
        logger.warning(f"Video note object is missing for user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id}")
        return

    logger.info(f"Processing video note from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id} (type: {chat.type}, group_id: {group_db_id}, forwarded: {is_forwarded})")
    try:
        await message.bot.send_chat_action(chat_id=chat.id, action="typing")
    except Exception as inner_e:
        logger.warning(f"Failed to send fallback chat action 'typing' to {chat.id}: {inner_e}")

    video_note = message.video_note
    video_data: bytes | None = None

    try:
        file = await message.bot.get_file(video_note.file_id)
        if not file.file_path:
            logger.error(f"File path is missing for video note file_id={video_note.file_id}")
            await send_error_message(message, "Помилка: не вдалося отримати шлях до файлу.")
            return
        downloaded_file = await message.bot.download_file(file.file_path)
        if downloaded_file is None:
            logger.error(f"Failed to download video note from path={file.file_path}, received None.")
            await send_error_message(message, "Помилка: не вдалося завантажити відео (отримано None).")
            return
        video_data = downloaded_file.read()
        logger.info(f"Downloaded {len(video_data)} bytes for video note from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id}")
    except (TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError) as e:
        logger.error(f"Telegram API error downloading video note file: {e}", exc_info=True)
        await send_error_message(message, f"Помилка мережі або API Telegram під час завантаження: {e}.")
        return
    except Exception as e:
        logger.error(f"Unexpected error downloading video note file: {e}", exc_info=True)
        await send_error_message(message, "Несподівана помилка при завантаженні файлу.")
        return

    if not video_data:
        logger.error(f"Video data is empty after download attempt for user {user_display_name} (ID: {user.telegram_id})")
        return

    try:
        # Process video data to ensure minimum duration
        processed_video_data = process_video_data(video_data)
        logger.info(f"Video processing complete: original size={len(video_data)}, processed size={len(processed_video_data)}")

        # Add message info first
        message_info = f"Message info: next message is video note from {user_display_name}"
        if is_forwarded and original_sender:
            message_info += f" (forwarded from {original_sender.full_name})"
        elif is_forwarded:
            message_info += " (forwarded from unknown user)"
            
        await message_dao.add_message(
            user_id=user.id, 
            role=MessageRole.USER, 
            text=message_info, 
            group_id=group_db_id,
            telegram_message_id=message.message_id
        )
        
        # Add video data - using the processed video data
        await message_dao.add_message(
            user_id=user.id, 
            role=MessageRole.USER, 
            video_data=processed_video_data,  # Using processed video data here
            group_id=group_db_id,
            telegram_message_id=message.message_id
        )
        logger.info(f"User video note message saved with processed video (user {user_display_name} (ID: {user.telegram_id}), group_id {group_db_id})")

        # Different handling for group vs private messages
        if group_db_id is not None:
            # For groups, limit history and add group context
            message_history = await message_dao.get_group_messages_as_contents(group_id=group_db_id, limit=500)
            logger.info(f"Retrieved {len(message_history)} messages from group history")
            
            # Add group context to the first message
            if message_history:
                group_context_text = f"Context: This is a group chat named '{group.name}'. The video note is from {user_display_name} (ID: {user.telegram_id})"
                if is_forwarded and original_sender:
                    group_context_text += f" (forwarded from {original_sender.full_name})"
                elif is_forwarded:
                    group_context_text += " (forwarded from unknown user)"
                group_context_text += ". Please analyze the video note and provide a concise response."
                
                # Add user context for non-forwarded messages
                if not is_forwarded:
                    user_context = gemini_types.Content(
                        parts=[gemini_types.Part(text=f"User context: This is a video note from {user_display_name} (ID: {user.telegram_id}) in the group chat '{group.name}'. Please analyze the video note and provide a concise response.")],
                        role="user"
                    )
                    message_history = [user_context] + message_history
                    logger.info("Added user context for non-forwarded message")
                
                group_context = gemini_types.Content(
                    parts=[gemini_types.Part(text=group_context_text)],
                    role="user"
                )
                message_history = [group_context] + message_history
                logger.info("Added group context to message history")
        else:
            # For private messages, use full history
            message_history = await message_dao.get_user_private_messages_as_contents(user_id=user.id)
            logger.info(f"Retrieved {len(message_history)} messages from private history")

        logger.info(f"Fetched {len(message_history)} messages for context (user {user_display_name} (ID: {user.telegram_id}), group_id {group_db_id})")

        if not message_history:
            logger.warning(f"Message history is empty before calling Gemini for user {user_display_name} (ID: {user.telegram_id}), group_id {group_db_id}")
            await send_error_message(message, "Помилка: не вдалося отримати історію повідомлень.")
            return

        # Use group-level control if available, otherwise fallback to user
        transcribe_video_note = user.transcribe_video_note
        if group and hasattr(group, 'transcribe_video_note'):
            transcribe_video_note = transcribe_video_note or group.transcribe_video_note
        generate_full_response = not transcribe_video_note
        logger.info(f"Calling AI for video note. Generate response based on user/group setting: {generate_full_response} (user {user_display_name} (ID: {user.telegram_id}), group_id {group_db_id})")

        try:
            gemini_result = await get_video_response(
                message_history=message_history,
                user=user,
                response=generate_full_response,
                message=message
            )

            if isinstance(gemini_result, str):
                # If we got a string error message, send it to the user
                await send_error_message(message, gemini_result)
                return

            await handle_gemini_result(
                gemini_result,
                message,
                message_dao=message_dao,
                user_dao=user_dao,
                user=user,
                group_db_id=group_db_id
            )
        except Exception as e:
            logger.error(f"Error calling Gemini API for video note: {e}", exc_info=True)
            await send_error_message(message, "Помилка під час обробки відео. Спробуйте пізніше.")
            return

    except Exception as e:
        logger.error(f"Handler error processing video note for user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id}: {e}", exc_info=True)
        await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки вашого відео повідомлення.")