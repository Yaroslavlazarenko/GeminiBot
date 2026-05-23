from aiogram import Router, filters
from aiogram.types import Message
from typing import Dict, Any

router = Router()

@router.message(filters.Command("start", "help"))
async def start_command(message: Message, user: Dict[str, Any]):
    text = (
        f"Hello, {user.get('first_name', 'User')}!\n\n"
        "I am a Gemini AI assistant. Send me a text message or a photo, and I will reply!"
    )
    await message.answer(text)

@router.message(filters.Command("clear"))
async def clear_command(message: Message, db_manager: Any, context_id: int, is_group: bool):
    if is_group:
        await db_manager.clear_group_history(context_id)
    else:
        await db_manager.clear_user_history(context_id)
    
    await message.reply("Context history has been cleared.")
