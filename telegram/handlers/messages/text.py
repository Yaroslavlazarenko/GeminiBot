import logging
from aiogram import Router, F
from aiogram.types import Message
from database.manager import ChatContext
from services.ai_service import get_ai_service

logger = logging.getLogger(__name__)
router = Router()
ai_service = get_ai_service()

@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_message(message: Message, chat_context: ChatContext):
    
    if chat_context.is_disabled or not chat_context.responds_to("text"):
        return

    text = message.text
    
    # Show typing action
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Generate Response
    response_text = await ai_service.generate_response(text, chat_context)

    # Save User Message to DB via the Context abstraction
    await chat_context.add_message("user", text, message.message_id)

    # Send response
    bot_message = await message.reply(response_text)

    # Save Bot Message to DB
    await chat_context.add_message("model", response_text, bot_message.message_id)
