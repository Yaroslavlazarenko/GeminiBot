import logging
import io
import tempfile
import os
from PIL import Image
import numpy as np
import cv2
from aiogram import F, Router
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError
from google.genai import types

from ai.gemini_client import get_video_response
from database.models import User, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
from ..utils import send_error_message, get_group_or_none, handle_gemini_result

logger = logging.getLogger(__name__)
router = Router()

def process_video_data(video_data: bytes) -> bytes:
    """Process video data to ensure minimum duration of 2.0 seconds."""
    try:
        logger.info(f"Starting video processing. Input size: {len(video_data)} bytes")
        
        # Create a temporary file on disk
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_file:
            temp_file.write(video_data)
            temp_file.flush()
            logger.info(f"Created temporary file: {temp_file.name}")
            
            # Use OpenCV to read video properties
            cap = cv2.VideoCapture(temp_file.name)
            if not cap.isOpened():
                logger.error("Failed to open video file")
                return video_data
                
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = frame_count / fps if fps > 0 else 0
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            
            logger.info(f"Original video properties: duration={duration:.2f}s, fps={fps}, frames={frame_count}, resolution={width}x{height}")
            
            if duration >= 2.0:
                # Video is long enough, return as is
                logger.info("Video is already long enough, returning original")
                cap.release()
                os.unlink(temp_file.name)
                return video_data
            
            # Video is too short, we need to extend it
            logger.info(f"Video is too short ({duration:.2f}s), will extend to 2.0s")
            
            # Get the last frame
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_count - 1)
            ret, last_frame = cap.read()
            if not ret:
                logger.error("Failed to read last frame")
                cap.release()
                os.unlink(temp_file.name)
                return video_data
            
            # Calculate how many frames we need to add
            frames_needed = int((2.0 - duration) * fps)
            logger.info(f"Need to add {frames_needed} frames to reach 2.0s")
            
            # Create a new video writer with better encoding settings
            output_path = temp_file.name + '_extended.mp4'
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')  # Using MPEG-4 codec
            out = cv2.VideoWriter(
                output_path,
                fourcc,
                fps,
                (width, height),
                isColor=True
            )
            
            if not out.isOpened():
                logger.error("Failed to create output video writer")
                cap.release()
                os.unlink(temp_file.name)
                return video_data
            
            # Write original frames
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            frames_written = 0
            for _ in range(frame_count):
                ret, frame = cap.read()
                if ret:
                    out.write(frame)
                    frames_written += 1
            
            logger.info(f"Wrote {frames_written} original frames")
            
            # Add additional frames
            for _ in range(frames_needed):
                out.write(last_frame)
                frames_written += 1
            
            logger.info(f"Added {frames_needed} additional frames, total frames written: {frames_written}")
            
            # Clean up
            cap.release()
            out.release()
            os.unlink(temp_file.name)
            
            # Verify the extended video
            cap = cv2.VideoCapture(output_path)
            if not cap.isOpened():
                logger.error("Failed to verify extended video")
                os.unlink(output_path)
                return video_data
                
            extended_fps = cap.get(cv2.CAP_PROP_FPS)
            extended_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            extended_duration = extended_frames / extended_fps if extended_fps > 0 else 0
            logger.info(f"Extended video properties: duration={extended_duration:.2f}s, fps={extended_fps}, frames={extended_frames}")
            
            # Read the extended video
            with open(output_path, 'rb') as f:
                extended_data = f.read()
            
            os.unlink(output_path)
            cap.release()
            
            logger.info(f"Video processing complete: original size={len(video_data)}, extended size={len(extended_data)}")
            return extended_data
            
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

    # Log detailed user information
    logger.info(f"Processing video note from user: name={user_display_name}, id={user.telegram_id}, username={message.from_user.username}, is_bot={message.from_user.is_bot}")
    logger.info(f"User settings: responds_to_voice={user.responds_to_voice}, transcribe_voice_only={user.transcribe_voice_only}")

    # Check if message is forwarded
    is_forwarded = message.forward_from is not None or message.forward_from_chat is not None
    if is_forwarded:
        original_sender = message.forward_from
        if original_sender:
            logger.info(f"Processing forwarded video note from original sender {original_sender.full_name} (ID: {original_sender.id})")
        else:
            logger.info(f"Processing forwarded video note from unknown original sender")

    if not user.responds_to_voice:  # Using the same setting as voice messages
        logger.debug(f"Ignoring video note from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id} due to USER settings.")
        return
    if group and not group.responds_to_voice:  # Using the same setting as voice messages
        logger.debug(f"Ignoring video note from user {user_display_name} (ID: {user.telegram_id}) in group chat {chat.id} (DB ID: {group.id}) due to GROUP settings.")
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
                    user_context = types.Content(
                        parts=[types.Part(text=f"User context: This is a video note from {user_display_name} (ID: {user.telegram_id}) in the group chat '{group.name}'. Please analyze the video note and provide a concise response.")],
                        role="user"
                    )
                    message_history = [user_context] + message_history
                    logger.info("Added user context for non-forwarded message")
                
                group_context = types.Content(
                    parts=[types.Part(text=group_context_text)],
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

        generate_full_response = not user.transcribe_voice_only
        logger.info(f"Calling AI for video note. Generate response based on user setting: {generate_full_response} (user {user_display_name} (ID: {user.telegram_id}), group_id {group_db_id})")

        try:
            gemini_result = await get_video_response(
                message_history=message_history,
                user=user,
                response=generate_full_response
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