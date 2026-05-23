import logging
from aiogram import Router, F
from aiogram.types import Message
from typing import Dict, Any
from database.manager import DatabaseManager
from services.ai_service import AIService
from config import Config

logger = logging.getLogger(__name__)
router = Router()
config = Config()
ai_service = AIService(config)

@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_message(
    message: Message, 
    db_manager: DatabaseManager, 
    history_context: list, 
    context_id: int, 
    is_group: bool,
    user: Dict[str, Any],
    group: Dict[str, Any] = None
):
    # Check settings
    settings = group.get("settings", {}) if is_group else user.get("settings", {})
    if settings.get("is_global_disabled", False) or not settings.get("responds_to_text", True):
        return

    text = message.text
    
    # Show typing action
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Generate Response
    response_text = await ai_service.generate_response(text, history_context)

    # Save User Message to DB
    user_msg_doc = {"role": "user", "text": text, "message_id": message.message_id}
    if is_group:
        await db_manager.append_group_history(context_id, user_msg_doc)
    else:
        await db_manager.append_user_history(context_id, user_msg_doc)

    # Send response
    bot_message = await message.reply(response_text)

    # Save Bot Message to DB
    bot_msg_doc = {"role": "model", "text": response_text, "message_id": bot_message.message_id}
    if is_group:
        await db_manager.append_group_history(context_id, bot_msg_doc)
    else:
        await db_manager.append_user_history(context_id, bot_msg_doc)
