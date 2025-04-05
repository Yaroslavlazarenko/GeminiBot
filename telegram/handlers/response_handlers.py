# telegram/handlers/response_handlers.py

from aiogram import F, Router, filters
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from gemini.get_responses import get_text_response, get_audio_response
from services.database.models import User, MessageRole
from services.database.dao import DAO
import logging

logger = logging.getLogger(__name__)
router = Router()


# --- Utility functions ---
async def send_error_message(message: Message, error_text: str) -> None:
    """Sends an error message to the user."""
    await message.answer(error_text, parse_mode="Markdown")


async def log_and_reply(message: Message, log_message: str, reply_text: str, level: int = logging.INFO) -> None:
    """Logs a message and sends a reply to the user."""
    logger.log(level, log_message)
    await message.answer(reply_text, parse_mode="Markdown")


# --- Command handlers ---

@router.message(filters.Command("settings"))
async def show_settings_handler(message: Message, user: User) -> None:
    """Показывает текущие настройки пользователя с Markdown."""
    text_status = f"✅ *Увімкнено*" if user.responds_to_text else f"❌ *Вимкнено*"
    voice_status = f"✅ *Увімкнено*" if user.responds_to_voice else f"❌ *Вимкнено*"
    if user.responds_to_voice:
        voice_mode = "*📝 Тільки транскрипція*" if user.transcribe_voice_only else "*💬 Відповідь на повідомлення*"
    else:
        voice_mode = "_(неактуально)_" # Используем курсив для неактуального статуса

    # Используем Markdown: ** для жирного, ` для кода (команд)
    settings_text = (
        "⚙️ **Ваші поточні налаштування:**\n\n"
        f"Відповіді на текстові повідомлення: {text_status}\n"
        f"Обробка голосових повідомлень: {voice_status}\n"
        f"   - Режим: {voice_mode}\n\n"
        "Для зміни налаштувань використовуйте команди:\n"
        f"- `{'/toggletext'}` - увімк./вимк. відповіді на текст\n"
        f"- `{'/togglevoice'}` - увімк./вимк. обробку голосу\n"
        f"- `{'/togglemode'}` - перемкнути режим обробки голосу (відповідь/транскрипція)"
    )
    # Важно: указываем parse_mode="Markdown"
    await message.answer(settings_text, parse_mode="Markdown")


@router.message(filters.Command("toggletext"))
async def toggle_text_response_handler(message: Message, user: User) -> None:
    """Переключает настройку ответа на текст с Markdown ответом."""
    user.responds_to_text = not user.responds_to_text
    # Модифицируем save/update логику user здесь или в middleware
    log_message = f"User {user.telegram_id} toggled responds_to_text to {user.responds_to_text}"
    status = "*увімкнено*" if user.responds_to_text else "*вимкнено*" # Делаем статус жирным
    reply_text = f"✅ Відповіді на текстові повідомлення тепер {status}."
    # Убедитесь, что log_and_reply отправляет с parse_mode="Markdown"
    await log_and_reply(message, log_message, reply_text)


@router.message(filters.Command("togglevoice"))
async def toggle_voice_response_handler(message: Message, user: User) -> None:
    """Переключает настройку обработки голоса с Markdown ответом."""
    user.responds_to_voice = not user.responds_to_voice
    # Модифицируем save/update логику user здесь или в middleware
    log_message = f"User {user.telegram_id} toggled responds_to_voice to {user.responds_to_voice}"
    status = "*увімкнено*" if user.responds_to_voice else "*вимкнено*" # Делаем статус жирным
    reply_text = f"✅ Обробка голосових повідомлень тепер {status}."
    # Убедитесь, что log_and_reply отправляет с parse_mode="Markdown"
    await log_and_reply(message, log_message, reply_text)


@router.message(filters.Command("togglemode"))
async def toggle_voice_mode_handler(message: Message, user: User) -> None:
    """Переключает режим обработки голоса с Markdown ответом."""
    if not user.responds_to_voice:
        # Форматируем команду как код
        await message.answer(f"Спочатку увімкніть обробку голосових повідомлень командою `{'/togglevoice'}`.", parse_mode="Markdown")
        return

    user.transcribe_voice_only = not user.transcribe_voice_only
    # Модифицируем save/update логику user здесь или в middleware
    log_message = f"User {user.telegram_id} toggled transcribe_voice_only to {user.transcribe_voice_only}"
    mode = "*тільки транскрибувати*" if user.transcribe_voice_only else "*відповідати на повідомлення*" # Делаем режим жирным
    reply_text = f"✅ Режим обробки голосових змінено: бот буде {mode}."
    # Убедитесь, что log_and_reply отправляет с parse_mode="Markdown"
    await log_and_reply(message, log_message, reply_text)


@router.message(filters.Command("clear"))
async def clear_history_handler(message: Message, dao: DAO, user: User) -> None:
    """Очищает историю сообщений пользователя."""
    logger.info(f"User {user.telegram_id} requested history clear.")
    deleted_count = await dao.clear_history(user.id)
    await message.answer(f"🗑 Історію повідомлень очищено (видалено {deleted_count} повідомлень).")


# --- Message handlers ---

@router.message(F.text)
async def text_handler(message: Message, dao: DAO, user: User) -> None:
    """Обрабатывает текстовые сообщения, учитывая ответы."""
    if not user.responds_to_text:
        logger.debug(f"Ignoring text message from user {user.telegram_id} due to settings.")
        return

    # Убедимся, что в сообщении есть текст (хотя F.text уже это проверяет)
    if not message.text:
        logger.debug(f"Ignoring message without text content from user {user.telegram_id}")
        return

    logger.info(f"Received text message from user {user.telegram_id}")
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        text_to_save = message.text  # Текст для сохранения в БД по умолчанию

        # Проверяем, является ли сообщение ответом, есть ли текст в исходном сообщении
        # и не является ли исходное сообщение аудио
        if (message.reply_to_message
                and message.reply_to_message.text
                and not message.reply_to_message.audio):
            original_text = message.reply_to_message.text
            reply_text = message.text
            # Формируем специальный текст для сохранения в БД
            text_to_save = f"Пользователь ответил: {reply_text}. В ответ на сообщение: {original_text}"
            logger.info(f"Formatted message as reply context for user {user.telegram_id}")

        # Сохраняем (возможно, отформатированный) текст в БД
        await dao.add_message(user_id=user.id, role=MessageRole.USER, text=text_to_save)

        # Получаем историю сообщений (она будет включать отформатированное сообщение)
        message_history = await dao.get_user_messages_as_contents(user.id)

        # Важно: Передаем *оригинальный* текст пользователя в get_text_response,
        # чтобы ИИ отвечал на фактический ввод пользователя, а не на форматированную строку.
        # Если нужно, чтобы ИИ знал контекст ответа, можно передать text_to_save, но это менее типично.
        response_text = await get_text_response(message.text, message_history)

        if response_text:
            logger.info(f"Generated text response for user {user.telegram_id}")
            # Сохраняем ответ модели в БД
            await dao.add_message(user_id=user.id, role=MessageRole.MODEL, text=response_text)
            # Отправляем ответ пользователю
            response_lines = response_text.split("\\n")
            for line in response_lines:
                line = line.lstrip()  # Убираем пробелы в начале
                if line.strip():  # Проверяем, что строка не пустая
                    await message.answer(line, parse_mode="Markdown")
        else:
            logger.warning(f"Failed to get text response from AI for user {user.telegram_id}")
            # Можно отправить сообщение об ошибке или стандартный ответ
            # await send_error_message(message, "Не вдалося згенерувати відповідь.")

    except Exception as e:
        logger.error(f"Error processing text message for user {user.telegram_id}: {e}", exc_info=True)
        await send_error_message(message, "Сталася помилка під час обробки вашого повідомлення.")


@router.message(F.voice)
async def voice_handler(message: Message, dao: DAO, user: User) -> None:
    """Обрабатывает голосовые сообщения."""
    if not user.responds_to_voice:
        logger.debug(f"Ignoring voice message from user {user.telegram_id} due to settings.")
        return

    if not message.voice:
        logger.warning(f"Voice message object is missing for user {user.telegram_id}")
        return

    logger.info(f"Received voice message from user {user.telegram_id}")
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    voice = message.voice
    audio_bytes: bytes | None = None

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
        logger.debug(f"Downloaded {len(audio_bytes)} bytes for voice message from user {user.telegram_id}")

    except (TelegramBadRequest, TelegramNetworkError) as e:
        logger.error(f"Error downloading voice file for user {user.telegram_id}: {e}", exc_info=True)
        await send_error_message(message, f"Помилка завантаження файлу: {e}. Спробуйте ще раз.")
        return
    except Exception as e:
        logger.error(f"Unexpected error downloading voice file: {e}", exc_info=True)
        await send_error_message(message, "Сталася несподівана помилка при завантаженні файлу.")
        return

    if not audio_bytes:
        await send_error_message(message, "Не вдалося отримати дані голосового повідомлення.")
        return

    try:
        await dao.add_message(user_id=user.id, role=MessageRole.USER, audio_data=audio_bytes)
        message_history = await dao.get_user_messages_as_contents(user.id)
        generate_full_response = not user.transcribe_voice_only
        logger.debug(f"Calling get_audio_response for user {user.telegram_id} with generate_response={generate_full_response}")
        logger.debug(f"User transcribe_voice_only setting: {user.transcribe_voice_only}")

        response_text = await get_audio_response(audio_bytes, message_history, response=generate_full_response)

        if response_text:
            logger.info(f"Generated audio response/transcription for user {user.telegram_id}")
            await dao.add_message(user_id=user.id, role=MessageRole.MODEL, text=response_text)
            response_lines = response_text.split("\\n")
            for line in response_lines:
                line = line.lstrip()  # Убираем пробелы в начале
                if line.strip():  # Проверяем, что строка не пустая
                    await message.answer(line, parse_mode="Markdown")
        else:
            logger.warning(f"Failed to get audio response/transcription from Gemini for user {user.telegram_id}")
            await send_error_message(message, "На жаль, не вдалося обробити голосове повідомлення.")

    except Exception as e:
        logger.error(f"Error processing voice message via Gemini for user {user.telegram_id}: {e}", exc_info=True)
        await send_error_message(message, "Сталася помилка під час обробки вашого голосового повідомлення через AI.")