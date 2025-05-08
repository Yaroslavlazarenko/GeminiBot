import logging
import io
import tempfile
import os
from PIL import Image
import ffmpeg
from aiogram import F, Router, Bot
from aiogram.types import Message
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database.models import User, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO, StickerDAO
from ai.gemini_client import get_text_response
from ..utils import send_error_message, get_group_or_none, handle_gemini_result
from ..message_batcher import message_batcher, ProcessingCallback

logger = logging.getLogger(__name__)
router = Router()

# Define FFmpeg paths
FFMPEG_PATH = "/usr/bin/ffmpeg"
FFPROBE_PATH = "/usr/bin/ffprobe"

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
                return video_data

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

                # Optimize encoding settings for faster processing
                stream = stream.global_args('-c:v', 'libx264')
                stream = stream.global_args('-preset', 'fast')
                stream = stream.global_args('-crf', '23')
                stream = stream.global_args('-maxrate', '1M')
                stream = stream.global_args('-bufsize', '2M')
                stream = stream.global_args('-x264-params', 'keyint=any:scenecut=any')

                if audio is not None:
                    stream = stream.global_args('-c:a', 'aac')
                    stream = stream.global_args('-b:a', '128k')

                ffmpeg.run(stream, cmd=FFMPEG_PATH, overwrite_output=True, capture_stdout=True, capture_stderr=True)
                logger.info("Successfully processed video with ffmpeg")

                with open(output_file.name, 'rb') as f:
                    processed_data = f.read()

                os.unlink(input_file.name)
                os.unlink(output_file.name)

                logger.info(f"Video processing complete: original size={len(video_data)}, processed size={len(processed_data)}")
                return processed_data

            except ffmpeg.Error as e:
                logger.error(f"ffmpeg processing failed: {e.stderr.decode()}")
                os.unlink(input_file.name)
                if os.path.exists(output_file.name):
                    os.unlink(output_file.name)
                return video_data

    except Exception as e:
        logger.error(f"Error processing video: {e}", exc_info=True)
        return video_data

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
            logger.warning(f"Failed to send chat action to {chat_id} during batched sticker processing: {e}")

        # Вызываем AI модель
        gemini_result = await get_text_response(
            message_history=message_history,
            user=user,
            message=message
        )

        # Обрабатываем результат AI
        await handle_gemini_result(
            gemini_result,
            message,
            message_dao=message_dao,
            user_dao=user_dao,
            user=user,
            group_db_id=group_db_id
        )

        logger.info(f"Successfully processed batched sticker message for user {user_telegram_id} in chat {chat_id}")

    except Exception as e:
        logger.error(f"Error in batched sticker processing logic for user {user_telegram_id} in chat {chat_id} (last message ID: {message.message_id}): {e}", exc_info=True)
        try:
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
    user: User,
    session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Handles incoming sticker messages"""
    chat = message.chat
    user_telegram_id = message.from_user.id
    chat_id = chat.id
    user_display_name = message.from_user.full_name or f"User {user_telegram_id}"

    logger.debug(f"Received sticker message {message.message_id} from user {user_display_name} (ID: {user_telegram_id}) in chat {chat_id}. Saving to DB.")

    # --- Preliminary Checks (Do these immediately) ---
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
        sticker_file = await bot.download(sticker.file_id)
        if not sticker_file:
            logger.error(f"Failed to download sticker file for message {message.message_id}")
            await send_error_message(message, "Помилка: не вдалося завантажити файл стікера.")
            return

        # Get or create sticker in database
        async with session_factory() as session:
            sticker_dao = StickerDAO(session)

            # Process video sticker if needed
            sticker_data = sticker_file.read()
            is_video = sticker.is_video
            if is_video:
                logger.info(f"Processing video sticker with duration: {sticker.duration}s")
                processed_video_data = process_video_data(sticker_data)
                db_sticker = await sticker_dao.get_or_create_sticker(
                    telegram_sticker_id=sticker.file_id,
                    telegram_message_id=message.message_id,
                    name=sticker.set_name,
                    emoji=sticker.emoji,
                    video_data=processed_video_data
                )
                metadata += f", Type: video sticker, Duration: {sticker.duration}s"
            else:
                db_sticker = await sticker_dao.get_or_create_sticker(
                    telegram_sticker_id=sticker.file_id,
                    telegram_message_id=message.message_id,
                    name=sticker.set_name,
                    emoji=sticker.emoji,
                    image_data=sticker_data
                )
                metadata += ", Type: static sticker"
            await session.commit()

        # Save the message to the database with sticker reference
        await message_dao.add_message(
            user_id=user.id,
            role=MessageRole.USER,
            text=sticker.emoji or "[Sticker]",
            group_id=group_db_id,
            telegram_message_id=message.message_id,
            message_metadata=metadata,
            sticker_id=db_sticker.id  # Add reference to the sticker
        )
        logger.debug(f"User sticker message {message.message_id} saved to DB (user {user_telegram_id}, group_id {group_db_id}, sticker_id {db_sticker.id}).")

    except Exception as e:
        logger.error(f"Failed to save user sticker message {message.message_id} to DB: {e}", exc_info=True)
        await send_error_message(message, "Не вдалося зберегти ваш стікер.")
        return # Cannot proceed if message isn't saved

    # --- Pass to Batcher ---
    try:
        await message_batcher.handle_message(
            message=message,
            processing_callback=actual_sticker_processing_logic,
            session_factory=session_factory
        )
        logger.debug(f"Sticker message {message.message_id} from user {user_telegram_id} passed to batcher.")
    except Exception as e:
        logger.error(f"Error passing sticker message {message.message_id} to batcher for user {user_telegram_id}: {e}", exc_info=True)
        await send_error_message(message, "Виникла проблема з системою обробки стікерів. Спробуйте знову.")