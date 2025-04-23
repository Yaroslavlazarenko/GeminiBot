import logging

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

    if not user.responds_to_voice:  # Using the same setting as voice messages
        logger.debug(f"Ignoring video note from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id} due to USER settings.")
        return
    if group and not group.responds_to_voice:  # Using the same setting as voice messages
        logger.debug(f"Ignoring video note from user {user_display_name} (ID: {user.telegram_id}) in group chat {chat.id} (DB ID: {group.id}) due to GROUP settings.")
        return

    if not message.video_note:
        logger.warning(f"Video note object is missing for user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id}")
        return

    logger.info(f"Processing video note from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id} (type: {chat.type}, group_id: {group_db_id})")
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
        logger.debug(f"Downloaded {len(video_data)} bytes for video note from user {user_display_name} (ID: {user.telegram_id}) in chat {chat.id}")
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
        # Add message info first
        await message_dao.add_message(
            user_id=user.id, 
            role=MessageRole.USER, 
            text=f"Message info: next message is video note from {user_display_name}", 
            group_id=group_db_id,
            telegram_message_id=message.message_id
        )
        
        # Add video data
        await message_dao.add_message(
            user_id=user.id, 
            role=MessageRole.USER, 
            video_data=video_data, 
            group_id=group_db_id,
            telegram_message_id=message.message_id
        )
        logger.debug(f"User video note message queued for save (user {user_display_name} (ID: {user.telegram_id}), group_id {group_db_id})")

        # Different handling for group vs private messages
        if group_db_id is not None:
            # For groups, limit history and add group context
            message_history = await message_dao.get_group_messages_as_contents(group_id=group_db_id, limit=500)
            logger.debug(f"Retrieved {len(message_history)} messages from group history")
            
            # Add group context to the first message
            if message_history:
                group_context = types.Content(
                    parts=[types.Part(text=f"Context: This is a group chat named '{group.name}'. The video note is from {user_display_name} (ID: {user.telegram_id}). Please analyze the video note and provide a concise response.")],
                    role="user"
                )
                message_history = [group_context] + message_history
                logger.debug("Added group context to message history")
                
                # Log the structure of the first few messages
                for i, msg in enumerate(message_history[:3]):
                    logger.debug(f"Message {i+1} from start: role={msg.role}, parts={len(msg.parts) if msg.parts else 0}")
        else:
            # For private messages, use full history
            message_history = await message_dao.get_user_private_messages_as_contents(user_id=user.id)
            logger.debug(f"Retrieved {len(message_history)} messages from private history")

        logger.debug(f"Fetched {len(message_history)} messages for context (user {user_display_name} (ID: {user.telegram_id}), group_id {group_db_id})")

        if not message_history:
            logger.warning(f"Message history is empty before calling Gemini for user {user_display_name} (ID: {user.telegram_id}), group_id {group_db_id}")
            await send_error_message(message, "Помилка: не вдалося отримати історію повідомлень.")
            return

        # Log the structure of the last few messages
        for i, msg in enumerate(message_history[-3:]):
            logger.debug(f"Message {i+1} from end: role={msg.role}, parts={len(msg.parts) if msg.parts else 0}")

        generate_full_response = not user.transcribe_voice_only
        logger.debug(f"Calling AI for video note. Generate response based on user setting: {generate_full_response} (user {user_display_name} (ID: {user.telegram_id}), group_id {group_db_id})")

        try:
            # Add more detailed logging before calling Gemini
            logger.debug(f"Calling Gemini API with {len(message_history)} messages")
            for i, msg in enumerate(message_history):
                logger.debug(f"Message {i+1}: role={msg.role}, parts={len(msg.parts) if msg.parts else 0}")
                if msg.parts:
                    for j, part in enumerate(msg.parts):
                        if part is None:
                            logger.debug(f"  Part {j+1}: None")
                            continue
                        if hasattr(part, 'text') and part.text is not None:
                            logger.debug(f"  Part {j+1}: text={part.text[:100]}...")
                        elif hasattr(part, 'data') and part.data is not None:
                            logger.debug(f"  Part {j+1}: binary data (size={len(part.data)} bytes)")
                        else:
                            logger.debug(f"  Part {j+1}: unknown type")

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