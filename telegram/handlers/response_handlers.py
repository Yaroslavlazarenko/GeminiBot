from typing import List
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from aiogram import F, Router, types, filters
from aiogram.types import Message
import io
import logging

from gemini.get_responses import get_text_response, get_audio_response
from services.database.models import User
from services.database.dao import DAO

# Import your types and MessageHistory
from google.genai import types as google_types  # Assuming you're using google-genai types
from services.database.models import MessageHistory #Укажите путь к вашей модели MessageHistory

router = Router()

responseType = True
responsesToUser = True


@router.message(filters.Command("toggletextresponse"))
async def handler(message: Message) -> None:
    global responsesToUser
    responsesToUser = not responsesToUser

    if responsesToUser:
        await message.answer("Тепер бот відповідає")
    else:
        await message.answer("Тепер бот не відповідає")

@router.message(filters.Command("transcription"))
async def handler(message: Message) -> None:
    global responseType
    responseType = not responseType

    if responseType:
        await message.answer("Тепер бот відповідає на голосові")
    else:
        await message.answer("Тепер бот транспонує голосові")


@router.message(filters.Command("clear"))
async def clear_history_handler(message: Message, dao: DAO, user: User) -> None:
    """Clears the message history for the user."""
    await dao.clear_history(user.id)
    await message.answer("Історію повідомлень очищено.")


@router.message(F.text)
async def text_handler(message: Message, dao: DAO, user: User) -> None:
    if(not responsesToUser):
        return None
    """Handles text messages, saves them to the database, and gets a response."""

    await dao.add_message(user_id=user.id, role="user", text=message.text)  # Save the user's message

    # Get the user's message history as a list of types.Content
    message_history = await dao.get_user_messages_as_contents(user.id)

    response = await get_text_response(message.text, message_history)

    if response:
        await dao.add_message(user_id=user.id, role="model", text=response)  # Save the bot's response
        await message.answer(text=response)
    else:
        # Only save the error message if there was an actual error
        error_message = "I couldn't generate a response."
        await dao.add_message(user_id=user.id, role="model", text=error_message)
        await message.answer(error_message)


@router.message(F.voice)
async def voice_handler(message: Message, dao: DAO, user: User) -> None:
    if(not responsesToUser):
        return None
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
    audio_bytes = downloaded_file.read()  # Remove 'await' here

    # Save the voice message to the database
    await dao.add_message(user_id=user.id, role="user", audio_data=audio_bytes)  # Save raw bytes

    # Get the user's message history as a list of types.Content
    message_history = await dao.get_user_messages_as_contents(user.id)

    try:
        # Get the audio transcription/response
        response = await get_audio_response(audio_bytes, message_history, responseType) # Pass raw bytes

        # Save the bot's response
        await dao.add_message(user_id=user.id, role="model", text=response)

        await message.answer(response)
    except Exception as e:
        await dao.add_message(user_id=user.id, role="model", text=f"Failed to process audio: {e}")
        await message.answer("Failed to transcribe audio")