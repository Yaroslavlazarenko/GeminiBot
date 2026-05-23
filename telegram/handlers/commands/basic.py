from aiogram import Router, filters
from aiogram.types import Message
from database.manager import ChatContext

router = Router()

@router.message(filters.Command("start", "help"))
async def start_command(message: Message, chat_context: ChatContext):
    # Depending on context, personalize greeting
    name = chat_context.doc.get('first_name', 'User') if not chat_context.is_group else chat_context.doc.get('name', 'Group')
    text = (
        f"Hello, {name}!\n\n"
        "I am a Gemini AI assistant. Send me a text message or a photo, and I will reply!"
    )
    await message.answer(text)

@router.message(filters.Command("clear"))
async def clear_command(message: Message, chat_context: ChatContext):
    await chat_context.clear_history()
    await message.reply("Context history has been cleared.")
