import logging
from typing import Any

from aiogram import F, Router, types
from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent
from ai.gemini_client import get_audio_response
from database.models import User

logger = logging.getLogger(__name__)
router = Router()

@router.inline_query()
async def inline_transcribe_handler(inline_query: InlineQuery, user: User):
    """
    Inline handler for transcribing voice messages
    """
    # Check if the inline query is a reply to a voice message
    if not inline_query.reply_to_message or not inline_query.reply_to_message.voice:
        return

    try:
        # Download the voice file
        file = await inline_query.bot.get_file(inline_query.reply_to_message.voice.file_id)
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
            message=inline_query.reply_to_message
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