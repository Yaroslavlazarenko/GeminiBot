from aiogram import F, Router, types, filters
from gemini.get_responses import get_text_response, get_audio_response
from services.database.models import User
from services.database.dao import DAO
from aiogram.types import Message
import io

router = Router()

responseType = True


@router.message(filters.Command("tr"))
async def handler(message: Message) -> None:
    global responseType
    responseType = not responseType

    if responseType:
        await message.answer("Тепер бот відповідає на голосові")
    else:
        await message.answer("Тепер бот транспонує голосові")


@router.message(F.text)
async def text_handler(message: Message, dao: DAO, user: User) -> None:
    """Handles text messages, saves them to the database, and gets a response."""

    await dao.add_message(user_id=user.id, role="user", text=message.text)  # Save the user's message

    response = await get_text_response(message.text)

    if response:
        await dao.add_message(user_id=user.id, role="model", text=response)  # Save the bot's response
        await message.answer(text=response)
    else:
        await dao.add_message(user_id=user.id, role="model", text="I couldn't generate a response.")
        await message.answer("I couldn't generate a response.")


@router.message(F.voice)
async def voice_handler(message: Message, dao: DAO, user: User) -> None:
    """Handles voice messages, saves them to the database, and gets a response."""

    voice = message.voice

    if not voice:
        await message.answer("No voice message detected.")
        return

    # Download the audio file
    file = await message.bot.get_file(voice.file_id)
    file_path = file.file_path
    downloaded_file = await message.bot.download_file(file_path)

    # Get the raw bytes from the BytesIO object
    audio_bytes = downloaded_file.read()

    # Save the voice message to the database
    await dao.add_message(user_id=user.id, role="user", audio_data=audio_bytes)  # Save raw bytes

    try:
        # Get the audio transcription/response
        response = await get_audio_response(audio_bytes, responseType) # Pass raw bytes

        # Save the bot's response
        await dao.add_message(user_id=user.id, role="model", text=response)

        await message.answer(response)
    except Exception as e:
        await dao.add_message(user_id=user.id, role="model", text=f"Failed to process audio: {e}")
        await message.answer("Failed to transcribe audio")