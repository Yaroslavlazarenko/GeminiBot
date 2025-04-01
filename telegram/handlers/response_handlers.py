# telegram/handlers/response_handlers.py

from aiogram import F, Router, filters
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError # Для обработки ошибок скачивания

# Убираем импорт AsyncSession, т.к. коммит делает middleware
# from sqlalchemy.ext.asyncio import AsyncSession

from gemini.get_responses import get_text_response, get_audio_response
from services.database.models import User, MessageRole # Импортируем модель и Enum
from services.database.dao import DAO
import logging # Используем logging

logger = logging.getLogger(__name__)
router = Router()

# --- Команды для управления настройками ---

@router.message(filters.Command("settings"))
async def show_settings_handler(message: Message, user: User) -> None:
    """Показывает текущие настройки пользователя."""
    text_status = "✅ Увімкнено" if user.responds_to_text else "❌ Вимкнено"
    voice_status = "✅ Увімкнено" if user.responds_to_voice else "❌ Вимкнено"
    if user.responds_to_voice:
        voice_mode = "📝 Тільки транскрипція" if user.transcribe_voice_only else "💬 Відповідь на повідомлення"
    else:
        voice_mode = "(неактуально)"

    settings_text = (
        "⚙️ **Ваші поточні налаштування:**\n\n"
        f"Відповіді на текстові повідомлення: {text_status}\n"
        f"Обробка голосових повідомлень: {voice_status}\n"
        f"   - Режим: {voice_mode}\n\n"
        "Для зміни налаштувань використовуйте команди:\n"
        "- `/toggletext` - увімк./вимк. відповіді на текст\n"
        "- `/togglevoice` - увімк./вимк. обробку голосу\n"
        "- `/togglemode` - перемкнути режим обробки голосу (відповідь/транскрипція)"
    )
    await message.answer(settings_text, parse_mode="Markdown")


@router.message(filters.Command("toggletext"))
async def toggle_text_response_handler(message: Message, user: User) -> None:
    """Переключает настройку ответа на текстовые сообщения."""
    user.responds_to_text = not user.responds_to_text
    logger.info(f"User {user.telegram_id} toggled responds_to_text to {user.responds_to_text}")
    status = "увімкнено" if user.responds_to_text else "вимкнено"
    await message.answer(f"✅ Відповіді на текстові повідомлення тепер {status}.")
    # Middleware сохранит изменения user

@router.message(filters.Command("togglevoice"))
async def toggle_voice_response_handler(message: Message, user: User) -> None:
    """Переключает настройку обработки голосовых сообщений."""
    user.responds_to_voice = not user.responds_to_voice
    logger.info(f"User {user.telegram_id} toggled responds_to_voice to {user.responds_to_voice}")
    status = "увімкнено" if user.responds_to_voice else "вимкнено"
    await message.answer(f"✅ Обробка голосових повідомлень тепер {status}.")
    # Middleware сохранит изменения user

@router.message(filters.Command("togglemode"))
async def toggle_voice_mode_handler(message: Message, user: User) -> None:
    """Переключает режим обработки голосовых: ответ или транскрипция."""
    if not user.responds_to_voice:
        await message.answer("Спочатку увімкніть обробку голосових повідомлень командою `/togglevoice`.")
        return

    user.transcribe_voice_only = not user.transcribe_voice_only
    logger.info(f"User {user.telegram_id} toggled transcribe_voice_only to {user.transcribe_voice_only}")
    mode = "тільки транскрибувати" if user.transcribe_voice_only else "відповідати на повідомлення"
    await message.answer(f"✅ Режим обробки голосових змінено: бот буде {mode}.")
    # Middleware сохранит изменения user


# --- Команда очистки истории ---

@router.message(filters.Command("clear"))
async def clear_history_handler(message: Message, dao: DAO, user: User) -> None:
    """Очищает историю сообщений пользователя."""
    logger.info(f"User {user.telegram_id} requested history clear.")
    deleted_count = await dao.clear_history(user.id)
    await message.answer(f"🗑 Історію повідомлень очищено (видалено {deleted_count} повідомлень).")


# --- Обработчики сообщений ---

@router.message(F.text)
async def text_handler(message: Message, dao: DAO, user: User) -> None:
    """Обрабатывает текстовые сообщения."""
    if not user.responds_to_text:
        logger.debug(f"Ignoring text message from user {user.telegram_id} due to settings.")
        # Можно ничего не отвечать или дать подсказку
        # await message.answer("Відповіді на текст вимкнені. /settings")
        return

    if not message.text: # На всякий случай
        return

    logger.info(f"Received text message from user {user.telegram_id}")
    # Добавляем сообщение пользователя в историю
    await dao.add_message(user_id=user.id, role=MessageRole.USER, text=message.text)

    # Получаем историю для API
    message_history = await dao.get_user_messages_as_contents(user.id)

    # Получаем ответ от Gemini
    response_text = await get_text_response(message.text, message_history) # Передаем только текст текущего сообщения

    if response_text:
        logger.info(f"Generated text response for user {user.telegram_id}")
        # Добавляем ответ модели в историю
        await dao.add_message(user_id=user.id, role=MessageRole.MODEL, text=response_text)
        await message.answer(text=response_text)
    else:
        logger.warning(f"Failed to get text response from Gemini for user {user.telegram_id}")
        # Сообщаем пользователю об ошибке
        await message.answer("На жаль, не вдалося згенерувати відповідь. Спробуйте ще раз пізніше.")


@router.message(F.voice)
async def voice_handler(message: Message, dao: DAO, user: User) -> None:
    """Обрабатывает голосовые сообщения."""
    if not user.responds_to_voice:
        logger.debug(f"Ignoring voice message from user {user.telegram_id} due to settings.")
        # await message.answer("Обробка голосу вимкнена. /settings")
        return

    if not message.voice:
        logger.warning(f"Voice message object is missing for user {user.telegram_id}")
        return

    logger.info(f"Received voice message from user {user.telegram_id}")
    voice = message.voice
    audio_bytes: bytes | None = None

    # Скачиваем файл
    try:
        file = await message.bot.get_file(voice.file_id)
        if not file.file_path:
             logger.error(f"File path is missing for voice file_id={voice.file_id}")
             await message.answer("Помилка: не вдалося отримати шлях до файлу.")
             return

        downloaded_file = await message.bot.download_file(file.file_path)
        if downloaded_file is None:
            logger.error(f"Failed to download voice file from path={file.file_path}")
            await message.answer("Помилка: не вдалося завантажити голосове повідомлення.")
            return

        audio_bytes = downloaded_file.read()
        logger.debug(f"Downloaded {len(audio_bytes)} bytes for voice message from user {user.telegram_id}")

    except (TelegramBadRequest, TelegramNetworkError) as e:
        logger.error(f"Error downloading voice file for user {user.telegram_id}: {e}", exc_info=True)
        await message.answer(f"Помилка завантаження файлу: {e}. Спробуйте ще раз.")
        return
    except Exception as e: # Ловим другие возможные ошибки
        logger.error(f"Unexpected error downloading voice file: {e}", exc_info=True)
        await message.answer("Сталася несподівана помилка при завантаженні файлу.")
        return

    if not audio_bytes: # Если скачивание не удалось по какой-то причине
         await message.answer("Не вдалося отримати дані голосового повідомлення.")
         return

    # Добавляем аудио пользователя в историю
    await dao.add_message(user_id=user.id, role=MessageRole.USER, audio_data=audio_bytes)

    # Получаем историю для API
    message_history = await dao.get_user_messages_as_contents(user.id)

    # Получаем ответ от Gemini в соответствии с настройками пользователя
    try:
        # Определяем, нужен ли полный ответ или только транскрипция
        generate_full_response = not user.transcribe_voice_only
        logger.debug(f"Calling get_audio_response for user {user.telegram_id} with generate_response={generate_full_response}")
        logger.debug(f"User transcribe_voice_only setting: {user.transcribe_voice_only}") # to confirm user setting

        response_text = await get_audio_response(audio_bytes, message_history, response=generate_full_response) # pass under correct parameter name

        if response_text:
            logger.info(f"Generated audio response/transcription for user {user.telegram_id}")
            # Добавляем ответ/транскрипцию модели в историю
            await dao.add_message(user_id=user.id, role=MessageRole.MODEL, text=response_text)
            await message.answer(response_text)
        else:
            logger.warning(f"Failed to get audio response/transcription from Gemini for user {user.telegram_id}")
            await message.answer("На жаль, не вдалося обробити голосове повідомлення.")

    except Exception as e: # Ловим ошибки от get_audio_response или Gemini API
        logger.error(f"Error processing voice message via Gemini for user {user.telegram_id}: {e}", exc_info=True)
        await message.answer("Сталася помилка під час обробки вашого голосового повідомлення через AI.")