import logging
from aiogram import Router, F
from aiogram.types import Message
from database.manager import ChatContext
from services.ai_service import get_ai_service
from services.gatekeeper_service import get_gatekeeper
from core.enums import GatekeeperAction, ToolName

logger = logging.getLogger(__name__)
router = Router()
ai_service = get_ai_service()
gatekeeper = get_gatekeeper()

@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_message(message: Message, chat_context: ChatContext):
    
    if chat_context.is_disabled or not chat_context.responds_to("text"):
        return

    text = message.text

    # 1. Gatekeeper determines if a response is needed
    action = await gatekeeper.decide(text, chat_context)

    if action == GatekeeperAction.IGNORE:
        return
        
    if action == GatekeeperAction.DISABLE_RESPONSES:
        await chat_context.update_settings({"is_global_disabled": True})
        logger.info(f"Responses disabled for chat {chat_context.id}")
        return

    # 2. Proceed with Persona response
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Generate Response
    response_text, tool_calls = await ai_service.generate_response(text, chat_context)

    # Handle native Tool Calls via Enums
    for call in tool_calls:
        logger.info(f"LLM called tool: {call.name} with args {call.args}")
            
        if call.name == ToolName.ADD_REACTION.value:
            emoji = call.args.get("emoji")
            message_ids = call.args.get("message_ids", [message.message_id])
            if not message_ids:
                message_ids = [message.message_id]
                
            for m_id in message_ids:
                try:
                    await message.bot.set_message_reaction(
                        chat_id=message.chat.id, 
                        message_id=m_id, 
                        reaction=[{"type": "emoji", "emoji": emoji}]
                    )
                except Exception as e:
                    logger.error(f"Failed to add reaction {emoji} to {m_id}: {e}")
                    
        elif call.name == ToolName.REPLY_TO_MESSAGE.value:
            message.reply_to_message_id = call.args.get("message_id")

    # Save User Message to DB via the Context abstraction
    await chat_context.add_message("user", text, message.message_id)

    # Send text response if the model provided one
    if response_text:
        bot_message = await message.reply(
            response_text, 
            reply_to_message_id=getattr(message, 'reply_to_message_id', None)
        )
        # Save Bot Message to DB
        await chat_context.add_message("model", response_text, bot_message.message_id)

