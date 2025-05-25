import logging
from typing import Any

from aiogram import F, Router, types
from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent
from ai.gemini_client import get_audio_response
from database.models import User
from database.dao import UserDAO

logger = logging.getLogger(__name__)
router = Router()

@router.inline_query()
async def inline_transcribe_handler(
    inline_query: InlineQuery,
    user_dao: UserDAO,
) -> None:
    """
    Inline handler for transcribing voice messages
    """
    # Get user from database
    user = await user_dao.get_user_by_telegram_id(inline_query.from_user.id)
    if not user:
        # Create error result if user not found
        error_result = InlineQueryResultArticle(
            id="1",
            title="❌ Помилка",
            description="Користувача не знайдено",
            input_message_content=InputTextMessageContent(
                message_text="❌ Помилка: користувача не знайдено в базі даних."
            )
        )
        await inline_query.answer([error_result], cache_time=1)
        return

    # Check if query contains a message ID (for reply)
    if not inline_query.query:
        # Show usage instructions
        help_result = InlineQueryResultArticle(
            id="1",
            title="ℹ️ Як використовувати",
            description="Відповідайте на голосове повідомлення та виберіть цей бот",
            input_message_content=InputTextMessageContent(
                message_text="ℹ️ Відповідайте на голосове повідомлення та виберіть цей бот для отримання транскрипції."
            )
        )
        await inline_query.answer([help_result], cache_time=1)
        return

    try:
        # Get the message being replied to
        try:
            message = await inline_query.bot.get_chat_history(
                chat_id=inline_query.chat.id,
                limit=1,
                offset_id=inline_query.message_id
            )
            if not message or not message[0]:
                raise Exception("Message not found")
            message = message[0]
        except Exception as e:
            logger.error(f"Error getting message: {e}")
            error_result = InlineQueryResultArticle(
                id="1",
                title="❌ Помилка",
                description="Не вдалося знайти повідомлення",
                input_message_content=InputTextMessageContent(
                    message_text="❌ Не вдалося знайти повідомлення для транскрипції."
                )
            )
            await inline_query.answer([error_result], cache_time=1)
            return

        # Check if the message contains a voice
        if not message.voice:
            error_result = InlineQueryResultArticle(
                id="1",
                title="❌ Помилка",
                description="Повідомлення не містить голосового",
                input_message_content=InputTextMessageContent(
                    message_text="❌ Будь ласка, відповідайте на голосове повідомлення."
                )
            )
            await inline_query.answer([error_result], cache_time=1)
            return

        # Download the voice file
        file = await inline_query.bot.get_file(message.voice.file_id)
        if not file.file_path:
            return

        downloaded_file = await inline_query.bot.download_file(file.file_path)
        if downloaded_file is None:
            return

        voice_data = downloaded_file.read()

        # Create message history with the voice data
        message_history = [
            types.Content(
                parts=[types.Part(text="Transcribe this voice message")],
                role="user"
            ),
            types.Content(
                parts=[types.Part(voice_data=voice_data)],
                role="user"
            )
        ]

        # Get transcription using Gemini
        result = await get_audio_response(
            message_history=message_history,
            user=user,
            response=False,  # False means transcribe only
            message=message
        )

        if result and result.get("type") != "error":
            transcription = result.get("data", {}).get("text", "")
            if transcription:
                # Create inline result
                result = InlineQueryResultArticle(
                    id="1",
                    title="📝 Транскрипція голосового",
                    description=transcription[:100] + "..." if len(transcription) > 100 else transcription,
                    input_message_content=InputTextMessageContent(
                        message_text=f"📝 Транскрипція голосового:\n\n{transcription}"
                    )
                )
                await inline_query.answer([result], cache_time=1)
            else:
                # Show error result
                error_result = InlineQueryResultArticle(
                    id="1",
                    title="❌ Помилка транскрипції",
                    description="Не вдалося отримати транскрипцію",
                    input_message_content=InputTextMessageContent(
                        message_text="❌ Не вдалося отримати транскрипцію голосового повідомлення."
                    )
                )
                await inline_query.answer([error_result], cache_time=1)
        else:
            error_text = result.get("data", {}).get("text", "Невідома помилка") if result else "Невідома помилка"
            error_result = InlineQueryResultArticle(
                id="1",
                title="❌ Помилка",
                description=error_text,
                input_message_content=InputTextMessageContent(
                    message_text=f"❌ Помилка: {error_text}"
                )
            )
            await inline_query.answer([error_result], cache_time=1)

    except Exception as e:
        logger.error(f"Error in inline_transcribe_handler: {e}", exc_info=True)
        error_result = InlineQueryResultArticle(
            id="1",
            title="❌ Помилка",
            description="Сталася помилка під час обробки",
            input_message_content=InputTextMessageContent(
                message_text="❌ Сталася помилка під час обробки голосового повідомлення."
            )
        )
        await inline_query.answer([error_result], cache_time=1) 