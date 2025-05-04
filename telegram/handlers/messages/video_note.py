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

from ai.gemini_client import get_video_response, get_text_response
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
    """Обработчик видео-заметок"""
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
            logger.debug(f"Ignoring video note from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id} due to global USER disable.")
            return
        if group and group.is_global_disabled:
            logger.debug(f"Ignoring video note from user {user_display_name} (ID: {user.telegram_id}) in group chat {chat.id} due to global GROUP disable.")
            return

        # Check video note specific settings
        if not getattr(user, 'responds_to_video_note', True):
            logger.debug(f"Ignoring video note from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id} due to USER video note setting.")
            return
        if group and not getattr(group, 'responds_to_video_note', True):
            logger.debug(f"Ignoring video note from user {user_display_name} (ID: {user.telegram_id}) in group chat {chat.id} due to GROUP video note setting.")
            return

        video_note = message.video_note
        if not video_note:
            logger.error("Message marked as video note but no video note object found")
            await send_error_message(message, "Помилка: некоректні дані відео-нотатки.")
            return

        # Process video note
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

            # Transcribe if enabled
            transcription_text = None
            if getattr(user, 'transcribe_video_note', False):
                try:
                    logger.debug("Attempting to transcribe video note...")
                    # TODO: Add video note transcription implementation
                    pass
                except Exception as e:
                    logger.error(f"Error transcribing video note: {e}", exc_info=True)
                    await send_error_message(message, "Помилка: не вдалося транскрибувати відео-нотатку.")
                    return

        except Exception as e:
            logger.error(f"Error processing video note: {e}", exc_info=True)
            await send_error_message(message, "Помилка: не вдалося обробити відео-нотатку.")
            return

        # Формируем метаданные для видео-заметки
        is_forwarded = bool(message.forward_from or message.forward_from_chat or message.forward_sender_name or message.forward_date)
        
        if is_forwarded:
            # This is a forwarded video note
            metadata = f"Message info: FORWARDED video note shared by {user_display_name} (User ID: {user.telegram_id})"
            
            # Add detailed forwarding information
            if message.forward_from:
                # Forwarded from a user who hasn't restricted forwarding privacy
                forward_first_name = message.forward_from.first_name or ""
                forward_last_name = message.forward_from.last_name or ""
                forward_name = f"{forward_first_name} {forward_last_name}".strip() or message.forward_from.username or f"User {message.forward_from.id}"
                is_bot = "(Bot)" if message.forward_from.is_bot else ""
                metadata += f"\nOriginal sender: {forward_name} {is_bot} (ID: {message.forward_from.id})"
            elif message.forward_sender_name:
                # Forwarded from a user who restricted forwarding privacy
                metadata += f"\nOriginal sender: {message.forward_sender_name} (forwarding privacy enabled)"
            elif message.forward_from_chat:
                # Forwarded from a channel or group
                chat_type = message.forward_from_chat.type.capitalize()
                metadata += f"\nOriginal source: {chat_type} '{message.forward_from_chat.title}' (ID: {message.forward_from_chat.id})"
                if message.forward_signature:
                    metadata += f"\nPost author: {message.forward_signature}"
            
            # Add original message date if available
            if message.forward_date:
                metadata += f"\nOriginal message time: {message.forward_date}"
        else:
            # Regular non-forwarded video note
            metadata = f"Message info: video note from {user_display_name} (User ID: {user.telegram_id})"
        
        metadata += f", Duration: {video_note.duration}s, Message ID: {message.message_id}, Current time: {message.date}"
        if transcription_text:
            metadata += f"\nTranscription: {transcription_text}"

        # Add message to history with metadata
        await message_dao.add_message(
            user_id=user.id,
            role=MessageRole.USER,
            text=transcription_text,  # Use transcription as text if available
            group_id=group_db_id,
            telegram_message_id=message.message_id,
            message_metadata=metadata,
            video_data=video_data  # Save video note data
        )
        logger.debug(f"Video note message queued for save (user {user.telegram_id}, group_id {group_db_id})")

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
        logger.error(f"Handler error processing video note message for user {user.telegram_id} in chat {chat.id}: {e}", exc_info=True)
        await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки відео-нотатки.")