import logging
from aiogram import Router, filters, F
from aiogram.types import Message, BufferedInputFile
from core.database import ChatContext
from services.ai_service import get_ai_service
from services.gatekeeper_service import get_gatekeeper
from services.tts_service import get_tts_service
from core.config import Config
from bot.web_admin import create_admin_session

logger = logging.getLogger(__name__)

# Initialize the main router
router = Router()
ai_service = get_ai_service()
gatekeeper = get_gatekeeper()
tts_service = get_tts_service()
config = Config()

@router.message(filters.Command("admin"))
async def admin_command(message: Message, chat_context: ChatContext):
    # Check if the user is the authorized admin
    if message.from_user.id != config.admin_telegram_id:
        return
        
    token = create_admin_session()
    
    # We provide a hint that they need to replace the IP with their server's actual IP
    text = (
        f"🔐 **Admin Panel Access**\n\n"
        f"Here is your temporary, secure access link. Please open it in your browser:\n\n"
        f"`http://<YOUR_SERVER_IP>:{config.admin_port}/?token={token}`\n\n"
        f"_(Replace <YOUR_SERVER_IP> with the actual IP address of your server)._"
    )
    await message.answer(text, parse_mode="Markdown")

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
            
        elif call.name == ToolName.SEND_STICKER.value:
            emotion = call.args.get("emotion", "happy")
            # We will map emotions to a sticker later, for now we can send a mock message
            await message.answer(f"*(Отправляет {emotion} стикер)*", parse_mode="Markdown")
            
        elif call.name == ToolName.SEND_VOICE.value:
            text_to_speak = call.args.get("text_to_speak", "")
            
            # Show "recording voice" action
            await message.bot.send_chat_action(chat_id=message.chat.id, action="record_voice")
            
            # Generate the voice using ElevenLabs
            audio_bytes = await tts_service.generate_voice(text_to_speak)
            
            if audio_bytes:
                voice_file = BufferedInputFile(audio_bytes, filename="voice.ogg")
                await message.answer_voice(voice=voice_file)
            else:
                # Fallback to text if TTS fails or isn't configured
                await message.answer(f"*(Голосовое сообщение)*: {text_to_speak}", parse_mode="Markdown")
                
            # We don't want to send this as normal text too, so we clear the response_text
            response_text = ""

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

@router.message(F.photo | F.video | F.document | F.voice | F.video_note | F.sticker)
async def handle_media_message(message: Message, chat_context: ChatContext):
    
    if chat_context.is_disabled:
        return
        
    await message.reply("Media processing is currently being upgraded for the new architecture. It will be available shortly!")
