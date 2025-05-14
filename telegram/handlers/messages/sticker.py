import logging
import io
import tempfile
import os
import uuid
import asyncio
from pathlib import Path
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
# Check if we're on Windows or Linux and set paths accordingly
import platform
import shutil

# Detect the operating system
IS_WINDOWS = platform.system().lower() == 'windows'

# Try to find ffmpeg in PATH first
FFMPEG_BIN = shutil.which('ffmpeg')
FFPROBE_BIN = shutil.which('ffprobe')

# If not found, use default paths based on OS
if not FFMPEG_BIN:
    FFMPEG_BIN = 'ffmpeg' if IS_WINDOWS else '/usr/bin/ffmpeg'
if not FFPROBE_BIN:
    FFPROBE_BIN = 'ffprobe' if IS_WINDOWS else '/usr/bin/ffprobe'

# Check if FFmpeg is actually available and log detailed information
try:
    import subprocess
    ffmpeg_version_cmd = [FFMPEG_BIN, '-version']
    ffmpeg_version_output = subprocess.check_output(ffmpeg_version_cmd, stderr=subprocess.STDOUT, text=True)
    logger.info(f"FFmpeg version info: {ffmpeg_version_output.splitlines()[0]}")
    logger.info(f"FFmpeg is available at: {FFMPEG_BIN}")
except Exception as e:
    logger.error(f"Error checking FFmpeg availability: {e}")
    logger.warning("FFmpeg might not be properly installed or accessible")

logger.info(f"Using FFmpeg binary: {FFMPEG_BIN}")
logger.info(f"Using FFprobe binary: {FFPROBE_BIN}")
logger.info(f"Platform: {platform.system()} {platform.release()}")
logger.info(f"Python version: {platform.python_version()}")

# Define the directory for storing video stickers
# Use absolute path based on the module location for better cross-platform compatibility
MODULE_DIR = Path(__file__).parent.parent.parent.parent  # Go up to the project root
TEMP_STICKERS_DIR = MODULE_DIR / "temp_stickers"

# Ensure the directory exists
TEMP_STICKERS_DIR.mkdir(exist_ok=True)
logger.info(f"Temporary stickers directory: {TEMP_STICKERS_DIR.absolute()}")

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
            metadata = f"Next Sticker info: FORWARDED sticker shared by {user_display_name} (User ID: {user_telegram_id})"
            if message.forward_from:
                forward_name = message.forward_from.full_name or message.forward_from.username or f"User {message.forward_from.id}"
                is_bot = "(Bot)" if message.forward_from.is_bot else ""
                metadata += f"\nOriginal sender: {forward_name} {is_bot} (ID: {message.forward_from.id})"
            elif message.forward_sender_name:
                metadata += f"\nOriginal sender: {message.forward_sender_name} (forwarding privacy enabled)"
            elif message.forward_from_chat:
                chat_type = message.forward_from_chat.type.capitalize()
                metadata += f"\nOriginal source: {chat_type} '{message.forward_from_chat.title}'"
                if message.forward_signature:
                    metadata += f"\nPost author: {message.forward_signature}"
            if message.forward_date:
                metadata += f"\nOriginal message time: {message.forward_date}"
        else:
            metadata = f"Next sticker info: sticker from {user_display_name} (User ID: {user_telegram_id})"

        metadata += f", Set Name: {sticker.set_name or 'N/A'}, Emoji: {sticker.emoji or 'N/A'}, Message ID: {message.message_id}, Current time: {message.date}"

        # Определяем тип стикера
        is_video_sticker = sticker.is_video
        is_static_sticker = not sticker.is_animated and not sticker.is_video
        # Все стикеры, которые не являются видео-стикерами и не являются статическими, считаются TGS
        is_tgs_sticker = not is_video_sticker and not is_static_sticker
        sticker_data = None
        
        # Для TGS стикеров не загружаем файл, а только сохраняем метаданные
        if is_tgs_sticker:
            logger.info(f"TGS animated sticker detected (or other non-standard format), skipping file download for message {message.message_id}")
            metadata += ", Type: animated TGS sticker or other special format (metadata only)"
        else:
            # Для обычных стикеров загружаем файл как раньше
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
            
            # First check if this sticker already exists in the database
            existing_sticker = await sticker_dao.get_sticker_by_telegram_id(sticker.file_id)
            
            if existing_sticker:
                logger.info(f"Found existing sticker with ID {existing_sticker.id} for telegram_sticker_id {sticker.file_id}")
                db_sticker = existing_sticker
            else:
                # Проверяем тип стикера для сохранения
                # Используем ранее определенные переменные is_video_sticker, is_static_sticker и is_tgs_sticker
                if is_video_sticker:
                    logger.info(f"Processing new video sticker with audio track")
                    
                    # Generate a unique filename for the video sticker
                    file_uuid = str(uuid.uuid4())
                    original_extension = ".webm"  # Original sticker format
                    processed_extension = ".mp4"  # Processed format with audio
                    original_file_name = f"{file_uuid}{original_extension}"
                    processed_file_name = f"processed_{file_uuid}{processed_extension}"
                    original_file_path = TEMP_STICKERS_DIR / original_file_name
                    processed_file_path = TEMP_STICKERS_DIR / processed_file_name
                    
                    # Save the original video data to a file
                    with open(original_file_path, "wb") as f:
                        f.write(sticker_data)
                    
                    # Process with FFmpeg to add silent audio track
                    logger.debug(f"Adding audio track to video sticker: {original_file_path} -> {processed_file_path}")
                    try:
                        # First try to get video dimensions using ffprobe
                        video_width = None
                        video_height = None
                        try:
                            probe_cmd = [FFPROBE_BIN, '-v', 'error', '-select_streams', 'v:0',
                                        '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0',
                                        str(original_file_path)]
                            probe_process = await asyncio.create_subprocess_exec(
                                *probe_cmd,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE
                            )
                            probe_stdout, probe_stderr = await probe_process.communicate()
                            if probe_process.returncode == 0:
                                dimensions = probe_stdout.decode().strip()
                                logger.info(f"Video dimensions: {dimensions}")
                                if 'x' in dimensions:
                                    width_str, height_str = dimensions.split('x')
                                    video_width = int(width_str)
                                    video_height = int(height_str)
                        except Exception as probe_e:
                            logger.error(f"Error getting video dimensions: {probe_e}")
                        
                        # Check if we need to adjust dimensions
                        needs_dimension_fix = False
                        if video_width is not None and video_height is not None:
                            if video_width % 2 != 0 or video_height % 2 != 0:
                                needs_dimension_fix = True
                                logger.info(f"Video has odd dimensions ({video_width}x{video_height}), will adjust")
                        
                        # FFmpeg command to add silent audio track
                        if IS_WINDOWS or needs_dimension_fix:
                            # Windows or videos with odd dimensions - use pad filter
                            ffmpeg_command = [
                                FFMPEG_BIN,                                     # Use the detected FFmpeg binary
                                '-i', str(original_file_path),                  # Input 0 (video)
                                '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo', # Input 1 (silent audio)
                                '-vf', 'pad=width=ceil(iw/2)*2:height=ceil(ih/2)*2',  # Ensure even dimensions
                                '-c:v', 'libx264',                              # Video codec
                                '-pix_fmt', 'yuv420p',                          # Pixel format
                                '-c:a', 'aac',                                  # Audio codec
                                '-shortest',                                    # Match shortest stream
                                '-map', '0:v:0',                                # Map video from input 0
                                '-map', '1:a:0',                                # Map audio from input 1
                                '-y',                                           # Overwrite output
                                str(processed_file_path)                        # Output file
                            ]
                        else:
                            # Linux - simpler command without pad filter for better compatibility
                            ffmpeg_command = [
                                FFMPEG_BIN,                                     # Use the detected FFmpeg binary
                                '-i', str(original_file_path),                  # Input 0 (video)
                                '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo', # Input 1 (silent audio)
                                '-c:v', 'libx264',                              # Video codec
                                '-preset', 'fast',                              # Faster encoding
                                '-pix_fmt', 'yuv420p',                          # Pixel format
                                '-c:a', 'aac',                                  # Audio codec
                                '-strict', 'experimental',                       # Allow experimental codecs
                                '-shortest',                                    # Match shortest stream
                                '-y',                                           # Overwrite output
                                str(processed_file_path)                        # Output file
                            ]
                        
                        # Log the full FFmpeg command for debugging
                        ffmpeg_cmd_str = ' '.join(ffmpeg_command)
                        logger.info(f"Running FFmpeg command: {ffmpeg_cmd_str}")
                        
                        # Run FFmpeg command
                        process = await asyncio.create_subprocess_exec(
                            *ffmpeg_command,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        stdout, stderr = await process.communicate()
                        
                        if process.returncode != 0:
                            stderr_text = stderr.decode()
                            logger.error(f"FFmpeg error (return code {process.returncode}): {stderr_text}")
                            # Log more details about the input file
                            try:
                                probe_cmd = [FFPROBE_BIN, '-v', 'error', '-show_entries', 
                                            'stream=width,height,codec_name,pix_fmt', '-of', 'json', 
                                            str(original_file_path)]
                                probe_process = await asyncio.create_subprocess_exec(
                                    *probe_cmd,
                                    stdout=asyncio.subprocess.PIPE,
                                    stderr=asyncio.subprocess.PIPE
                                )
                                probe_stdout, probe_stderr = await probe_process.communicate()
                                if probe_process.returncode == 0:
                                    logger.info(f"Input file details: {probe_stdout.decode()}")
                                else:
                                    logger.error(f"FFprobe error: {probe_stderr.decode()}")
                            except Exception as probe_e:
                                logger.error(f"Error running FFprobe: {probe_e}")
                                
                            # If FFmpeg fails, fall back to original file
                            processed_file_path = original_file_path
                            processed_extension = original_extension
                            logger.warning(f"Falling back to original sticker file without audio")
                        else:
                            logger.info(f"Successfully added audio track to video sticker")
                            
                            # Clean up original file
                            try:
                                os.remove(original_file_path)
                                logger.debug(f"Removed original file: {original_file_path}")
                            except OSError as e:
                                logger.warning(f"Failed to remove original file {original_file_path}: {e}")
                    
                    except Exception as ffmpeg_e:
                        logger.error(f"Error processing video with FFmpeg: {ffmpeg_e}", exc_info=True)
                        # If any error occurs, use the original file
                        processed_file_path = original_file_path
                        processed_extension = original_extension
                        logger.warning(f"Falling back to original sticker file without audio")
                    
                    # Get the absolute URI for the processed file
                    file_uri = str(processed_file_path.absolute())
                    mime_type = "video/mp4" if processed_extension == ".mp4" else "video/webm"
                    
                    # Store the file path in the database
                    db_sticker = await sticker_dao.get_or_create_sticker(
                        telegram_sticker_id=sticker.file_id,
                        telegram_message_id=message.message_id,
                        name=sticker.set_name,
                        emoji=sticker.emoji,
                        file_path=file_uri,  # Store the file path
                        mime_type=mime_type  # Store the MIME type
                    )
                    metadata += f", Type: new video sticker with audio"
                elif is_tgs_sticker:
                    # Для TGS стикеров сохраняем только метаданные без изображения
                    db_sticker = await sticker_dao.get_or_create_sticker(
                        telegram_sticker_id=sticker.file_id,
                        telegram_message_id=message.message_id,
                        name=sticker.set_name,
                        emoji=sticker.emoji,
                        # Не передаем image_data, так как она нулевая
                    )
                    metadata += ", Type: animated TGS sticker (metadata only)"
                else:
                    # Для статических стикеров используем загруженные данные
                    db_sticker = await sticker_dao.get_or_create_sticker(
                        telegram_sticker_id=sticker.file_id,
                        telegram_message_id=message.message_id,
                        name=sticker.set_name,
                        emoji=sticker.emoji,
                        image_data=sticker_data # Use original data for static
                    )
                    metadata += ", Type: new static sticker"

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