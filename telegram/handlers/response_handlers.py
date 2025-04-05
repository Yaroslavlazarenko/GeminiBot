# telegram/handlers/response_handlers.py

import logging
import asyncio
from typing import cast, Dict, Any # Добавлены Dict, Any

from aiogram import F, Router, filters
from aiogram.types import Message, Chat
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError

# Обновляем импорты из gemini.get_responses
from gemini.get_responses import get_text_response, get_audio_response

from services.database.models import User, Group, MessageRole
from services.database.dao import AsyncDAO

logger = logging.getLogger(__name__)
router = Router()


# --- Utility functions (без изменений) ---
async def send_error_message(message: Message, error_text: str) -> None:
    """Sends an error message to the user/chat."""
    try:
        await message.answer(error_text, parse_mode="Markdown")
    except TelegramBadRequest as e:
        logger.warning(f"Failed to send error message to chat {message.chat.id}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error sending error message to chat {message.chat.id}: {e}", exc_info=True)

async def log_and_reply(message: Message, log_message: str, reply_text: str, level: int = logging.INFO) -> None:
    """Logs a message and sends a reply to the user/chat."""
    logger.log(level, log_message)
    try:
        await message.answer(reply_text, parse_mode="Markdown")
    except TelegramBadRequest as e:
        logger.warning(f"Failed to send reply message to chat {message.chat.id}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error sending reply message to chat {message.chat.id}: {e}", exc_info=True)


# --- Command handlers (без изменений, т.к. они не вызывают Gemini напрямую) ---
@router.message(filters.Command("settings"))
async def show_settings_handler(message: Message, user: User) -> None:
    """Показывает текущие настройки пользователя."""
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("ℹ️ Ці налаштування є глобальними для вас і впливають на мою поведінку у всіх чатах. Переглядати та змінювати їх найкраще у приватному чаті зі мною.")

    text_status = f"✅ *Увімкнено*" if user.responds_to_text else f"❌ *Вимкнено*"
    voice_status = f"✅ *Увімкнено*" if user.responds_to_voice else f"❌ *Вимкнено*"
    if user.responds_to_voice:
        voice_mode = "*📝 Тільки транскрипція*" if user.transcribe_voice_only else "*💬 Відповідь на повідомлення*"
    else:
        voice_mode = "_(неактуально)_"

    settings_text = (
        f"⚙️ **Ваші поточні налаштування (User ID: {user.telegram_id}):**\n\n"
        f"Відповіді на текстові повідомлення: {text_status}\n"
        f"Обробка голосових повідомлень: {voice_status}\n"
        f"   - Режим: {voice_mode}\n\n"
        "Для зміни налаштувань використовуйте команди:\n"
        f"- `{'/toggletext'}` - увімк./вимк. відповіді на текст\n"
        f"- `{'/togglevoice'}` - увімк./вимк. обробку голосу\n"
        f"- `{'/togglemode'}` - перемкнути режим обробки голосу"
    )
    await message.answer(settings_text, parse_mode="Markdown")

@router.message(filters.Command("toggletext"))
async def toggle_text_response_handler(message: Message, dao: AsyncDAO, user: User) -> None:
    """Переключает настройку ответа на текст."""
    if message.chat.type != ChatType.PRIVATE:
         await message.answer("ℹ️ Зверніть увагу: ця команда змінює ваші глобальні налаштування для всіх чатів.")

    new_value = not user.responds_to_text
    success = await dao.update_user_settings(user_id=user.id, responds_to_text=new_value)

    if success:
        user.responds_to_text = new_value
        log_message = f"User {user.telegram_id} toggled responds_to_text to {user.responds_to_text}"
        status = "*увімкнено*" if user.responds_to_text else "*вимкнено*"
        reply_text = f"✅ Відповіді на текстові повідомлення тепер {status}."
        await log_and_reply(message, log_message, reply_text)
    else:
        logger.error(f"Failed to update responds_to_text for user {user.telegram_id} in DB.")
        await send_error_message(message, "Не вдалося зберегти налаштування.")

@router.message(filters.Command("togglevoice"))
async def toggle_voice_response_handler(message: Message, dao: AsyncDAO, user: User) -> None:
    """Переключает настройку обработки голоса."""
    if message.chat.type != ChatType.PRIVATE:
         await message.answer("ℹ️ Зверніть увагу: ця команда змінює ваші глобальні налаштування для всіх чатів.")

    new_value = not user.responds_to_voice
    success = await dao.update_user_settings(user_id=user.id, responds_to_voice=new_value)

    if success:
        user.responds_to_voice = new_value
        log_message = f"User {user.telegram_id} toggled responds_to_voice to {user.responds_to_voice}"
        status = "*увімкнено*" if user.responds_to_voice else "*вимкнено*"
        reply_text = f"✅ Обробка голосових повідомлень тепер {status}."
        await log_and_reply(message, log_message, reply_text)
    else:
        logger.error(f"Failed to update responds_to_voice for user {user.telegram_id} in DB.")
        await send_error_message(message, "Не вдалося зберегти налаштування.")

@router.message(filters.Command("togglemode"))
async def toggle_voice_mode_handler(message: Message, dao: AsyncDAO, user: User) -> None:
    """Переключает режим обработки голоса."""
    if message.chat.type != ChatType.PRIVATE:
         await message.answer("ℹ️ Зверніть увагу: ця команда змінює ваші глобальні налаштування для всіх чатів.")

    if not user.responds_to_voice:
        await message.answer(f"Спочатку увімкніть обробку голосових повідомлень командою `{'/togglevoice'}`.", parse_mode="Markdown")
        return

    new_value = not user.transcribe_voice_only
    success = await dao.update_user_settings(user_id=user.id, transcribe_voice_only=new_value)

    if success:
        user.transcribe_voice_only = new_value
        log_message = f"User {user.telegram_id} toggled transcribe_voice_only to {user.transcribe_voice_only}"
        mode = "*тільки транскрибувати*" if user.transcribe_voice_only else "*відповідати на повідомлення*"
        reply_text = f"✅ Режим обробки голосових змінено: бот буде {mode}."
        await log_and_reply(message, log_message, reply_text)
    else:
        logger.error(f"Failed to update transcribe_voice_only for user {user.telegram_id} in DB.")
        await send_error_message(message, "Не вдалося зберегти налаштування.")

@router.message(filters.Command("clear"))
async def clear_history_handler(message: Message, dao: AsyncDAO, user: User) -> None:
    """Очищает историю сообщений пользователя (в текущем контексте)."""
    chat_type = message.chat.type
    deleted_count = 0
    context_msg = ""

    try:
        if chat_type == ChatType.PRIVATE:
            logger.info(f"User {user.telegram_id} requested history clear in private chat.")
            deleted_count = await dao.clear_history(user_id=user.id, group_id=None)
            context_msg = "вашу особисту історію повідомлень"
        elif chat_type in [ChatType.GROUP, ChatType.SUPERGROUP]:
            chat_id = message.chat.id
            group = await dao.get_group_by_telegram_id(telegram_chat_id=chat_id)
            if group:
                logger.info(f"User {user.telegram_id} requested history clear for themselves in group {chat_id} (DB ID: {group.id}).")
                deleted_count = await dao.clear_history(user_id=user.id, group_id=group.id)
                context_msg = f"ваші повідомлення у цьому чаті ('{group.name}')"
            else:
                logger.warning(f"User {user.telegram_id} tried to clear history in group {chat_id}, but group not found in DB.")
                await message.answer("Не вдалося знайти цей чат у базі даних для очищення історії.")
                return
        else:
            logger.warning(f"Unsupported chat type '{chat_type}' for /clear command from user {user.telegram_id}.")
            await message.answer("Ця команда підтримується лише в особистих чатах та групах.")
            return

        await message.answer(f"🗑 Історію очищено (видалено {deleted_count} повідомлень: {context_msg}).")

    except Exception as e:
        logger.error(f"Handler error during history clear for user {user.telegram_id} in chat {message.chat.id}: {e}", exc_info=True)
        await send_error_message(message, "Сталася помилка під час обробки команди очищення історії.")

# --- Message handlers ---

async def get_group_or_none(dao: AsyncDAO, chat: Chat) -> Group | None:
    """Вспомогательная функция для получения объекта Group или None."""
    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        try:
            chat_title = cast(str, chat.title) if chat.title else f"Group_{chat.id}"
            group = await dao.get_or_create_group(telegram_chat_id=chat.id, name=chat_title)
            return group
        except Exception as e:
            logger.error(f"Failed to get or create group for chat_id {chat.id}: {e}", exc_info=True)
            return None
    return None

# --- Вспомогательная функция для обработки результата Gemini ---
async def handle_gemini_result(
    gemini_result: Dict[str, Any],
    message: Message,
    dao: AsyncDAO,
    user: User,
    group_db_id: int | None
) -> None:
    """Обрабатывает структурированный ответ от Gemini API."""
    chat = message.chat
    result_type = gemini_result.get("type")
    result_data = gemini_result.get("data")

    if result_type == "text":
        response_text = result_data
        if response_text: # Дополнительная проверка, что текст не пустой
            logger.info(f"Gemini returned text for user {user.telegram_id} in chat {chat.id}. Saving and replying.")
            # 4. Сохраняем ответ модели
            await dao.add_message(
                user_id=user.id, role=MessageRole.MODEL, text=response_text, group_id=group_db_id
            )
            logger.debug(f"Model response queued for save (user {user.telegram_id}, group_id {group_db_id})")

            # 5. Отправляем ответ пользователю
            reply_method = message.reply if chat.type != ChatType.PRIVATE else message.answer
            response_lines = response_text.split("\\n")
            full_response_sent = ""
            for line in response_lines:
                line = line.lstrip()
                if line.strip():
                    try:
                         await reply_method(line, parse_mode="Markdown")
                         full_response_sent += line + "\n"
                         await asyncio.sleep(0.1)
                    except TelegramBadRequest as e:
                         logger.warning(f"Failed to send part of response (Markdown) to {chat.id}: {e}. Content: '{line[:50]}...'")
                         try:
                             await reply_method(line, parse_mode=None)
                             full_response_sent += line + "\n"
                         except Exception as inner_e:
                             logger.error(f"Failed to send part of response (no Markdown) to {chat.id}: {inner_e}")
                             break
                    except (TelegramNetworkError, Exception) as e:
                         logger.error(f"Error sending part of response to {chat.id}: {e}", exc_info=True)
                         break
            logger.debug(f"Finished sending response to chat {chat.id}. Approx length {len(full_response_sent)}")
        else:
             logger.warning(f"Gemini result type was 'text' but data was empty for user {user.telegram_id} in chat {chat.id}.")
             await send_error_message(message, "AI повернув порожню відповідь.")


    elif result_type == "function_call":
        function_name = result_data.get("name")
        # function_args = result_data.get("args", {}) # Аргументы пока не используются

        if function_name == "do_not_respond":
            logger.info(f"Function call '{function_name}' received for user {user.telegram_id} in chat {chat.id}. No reply sent.")
            # Ничего не делаем

        elif function_name == "disable_responses":
            logger.info(f"Function call '{function_name}' received. Disabling text responses for user {user.telegram_id}.")
            success = await dao.update_user_settings(user_id=user.id, responds_to_text=False)
            if success:
                user.responds_to_text = False # Обновляем в памяти
                await message.answer("⛔️ Я більше не буду відповідати на ваші текстові повідомлення за вашим запитом.")
            else:
                logger.error(f"Failed to disable responses for user {user.telegram_id} via DAO.")
                await send_error_message(message, "Не вдалося вимкнути відповіді. Спробуйте пізніше.")
        else:
            # Неизвестный вызов функции
             logger.warning(f"Received unknown function call '{function_name}' from Gemini.")
             # Можно отправить сообщение или проигнорировать


    elif result_type == "no_response":
        reason = result_data if isinstance(result_data, str) else "Reason not specified"
        logger.info(f"Gemini returned no response for user {user.telegram_id} in chat {chat.id}. Reason: {reason}")
        # Решаем, нужно ли уведомлять пользователя. Возможно, нет, если это do_not_respond или safety.
        # await send_error_message(message, "AI вирішив не відповідати або відповідь була заблокована.")

    elif result_type == "error":
        error_msg = result_data if isinstance(result_data, str) else "Unknown Gemini error"
        logger.error(f"Gemini API error for user {user.telegram_id} in chat {chat.id}: {error_msg}")
        await send_error_message(message, "Помилка під час звернення до AI. Спробуйте пізніше.")

    else:
        # Неизвестный тип результата
        logger.error(f"Received unknown result type from Gemini: {result_type}. Data: {result_data}")
        await send_error_message(message, "Отримано незрозумілий результат від AI.")


@router.message(F.text)
async def text_handler(message: Message, dao: AsyncDAO, user: User) -> None:
    """Обрабатывает текстовые сообщения в личных чатах и группах."""
    chat = message.chat
    group = await get_group_or_none(dao, chat)
    group_db_id = group.id if group else None

    if not user.responds_to_text:
        logger.debug(f"Ignoring text message from user {user.telegram_id} in chat {chat.id} due to user settings.")
        return
    if not message.text:
        logger.debug(f"Ignoring message without text content from user {user.telegram_id} in chat {chat.id}")
        return

    logger.info(f"Received text message from user {user.telegram_id} in chat {chat.id} (type: {chat.type}, group_id: {group_db_id})")
    try:
        await message.bot.send_chat_action(chat_id=chat.id, action="typing")
    except (TelegramNetworkError, TelegramBadRequest) as e:
         logger.warning(f"Failed to send chat action to {chat.id}: {e}")

    try:
        text_to_save = message.text
        if (message.reply_to_message
                and message.reply_to_message.text
                and not message.reply_to_message.audio
                and not message.reply_to_message.voice):
            original_text = message.reply_to_message.text
            reply_text = message.text
            text_to_save = f"Пользователь ответил: {reply_text}. В ответ на сообщение: {original_text}"
            logger.debug(f"Formatted reply text for saving: {text_to_save}")

        # 1. Сохраняем сообщение пользователя
        await dao.add_message(
            user_id=user.id, role=MessageRole.USER, text=text_to_save, group_id=group_db_id
        )
        logger.debug(f"User message queued for save (user {user.telegram_id}, group_id {group_db_id})")

        # 2. Получаем историю
        if group_db_id is not None:
            message_history = await dao.get_group_messages_as_contents(group_id=group_db_id)
        else:
            message_history = await dao.get_user_private_messages_as_contents(user_id=user.id)
        logger.debug(f"Fetched {len(message_history)} messages for context (user {user.telegram_id}, group_id {group_db_id})")

        # 3. Получаем ответ от AI
        # get_text_response теперь возвращает словарь
        gemini_result = await get_text_response(message_history=message_history, user=user)

        # 4. Обрабатываем результат Gemini
        await handle_gemini_result(gemini_result, message, dao, user, group_db_id)

    except Exception as e:
        logger.error(f"Handler error processing text message for user {user.telegram_id} in chat {chat.id}: {e}", exc_info=True)
        await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки вашого текстового повідомлення.")


@router.message(F.voice)
async def voice_handler(message: Message, dao: AsyncDAO, user: User) -> None:
    """Обрабатывает голосовые сообщения в личных чатах и группах."""
    chat = message.chat
    group = await get_group_or_none(dao, chat)
    group_db_id = group.id if group else None

    if not user.responds_to_voice:
        logger.debug(f"Ignoring voice message from user {user.telegram_id} in chat {chat.id} due to user settings.")
        return
    if not message.voice:
        logger.warning(f"Voice message object is missing for user {user.telegram_id} in chat {chat.id}")
        return

    logger.info(f"Received voice message from user {user.telegram_id} in chat {chat.id} (type: {chat.type}, group_id: {group_db_id})")
    try:
        await message.bot.send_chat_action(chat_id=chat.id, action="typing")
    except (TelegramNetworkError, TelegramBadRequest) as e:
         logger.warning(f"Failed to send chat action to {chat.id}: {e}")

    voice = message.voice
    audio_bytes: bytes | None = None

    # --- Download Voice File ---
    try:
        file = await message.bot.get_file(voice.file_id)
        if not file.file_path:
            logger.error(f"File path is missing for voice file_id={voice.file_id}")
            await send_error_message(message, "Помилка: не вдалося отримати шлях до файлу.")
            return
        downloaded_file = await message.bot.download_file(file.file_path)
        if downloaded_file is None:
            logger.error(f"Failed to download voice file from path={file.file_path}")
            await send_error_message(message, "Помилка: не вдалося завантажити голосове повідомлення.")
            return
        audio_bytes = downloaded_file.read()
        logger.debug(f"Downloaded {len(audio_bytes)} bytes for voice message from user {user.telegram_id} in chat {chat.id}")
    except (TelegramBadRequest, TelegramNetworkError) as e:
        logger.error(f"Telegram API error downloading voice file: {e}", exc_info=True)
        await send_error_message(message, f"Помилка мережі або API Telegram: {e}.")
        return
    except Exception as e:
        logger.error(f"Unexpected error downloading voice file: {e}", exc_info=True)
        await send_error_message(message, "Несподівана помилка при завантаженні файлу.")
        return

    if not audio_bytes:
        logger.error(f"Audio data is empty after download attempt for user {user.telegram_id}")
        await send_error_message(message, "Не вдалося отримати дані голосового повідомлення.")
        return

    # --- Process Voice with AI ---
    try:
        # 1. Сохраняем голосовое
        await dao.add_message(
            user_id=user.id, role=MessageRole.USER, audio_data=audio_bytes, group_id=group_db_id
        )
        logger.debug(f"User voice message queued for save (user {user.telegram_id}, group_id {group_db_id})")

        # 2. Получаем историю
        if group_db_id is not None:
            message_history = await dao.get_group_messages_as_contents(group_id=group_db_id)
        else:
            message_history = await dao.get_user_private_messages_as_contents(user_id=user.id)
        logger.debug(f"Fetched {len(message_history)} messages for context (user {user.telegram_id}, group_id {group_db_id})")

        # Определяем режим работы AI
        generate_full_response = not user.transcribe_voice_only
        logger.debug(f"Calling AI for voice. Generate response: {generate_full_response} (user {user.telegram_id}, group_id {group_db_id})")

        # 3. Получаем ответ/транскрипцию от AI
        # get_audio_response теперь возвращает словарь
        gemini_result = await get_audio_response(
            message_history=message_history, # Передаем историю с аудио Part
            user=user,
            response=generate_full_response
        )

        # 4. Обрабатываем результат Gemini
        await handle_gemini_result(gemini_result, message, dao, user, group_db_id)

    except Exception as e:
        logger.error(f"Handler error processing voice message for user {user.telegram_id} in chat {chat.id}: {e}", exc_info=True)
        await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки вашого голосового повідомлення.")