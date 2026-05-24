import logging
import asyncio
from aiogram import Router, filters, F
from aiogram.types import Message, BufferedInputFile, MessageReactionUpdated
from aiogram.utils.chat_action import ChatActionSender
from core.database import ChatContext
from services.ai_service import get_ai_service
from services.gatekeeper_service import get_gatekeeper
from services.tts_service import get_tts_service
from services.media_service import MediaService
from services.transcription_service import get_transcription_service
from core.config import Config
from bot.web_admin import create_admin_session
from core.enums import GatekeeperAction, ToolName

import time
from services.avatar_service import AvatarService

logger = logging.getLogger(__name__)

# Simple in-memory cache for sticker sets to prevent Telegram API rate limits
sticker_cache = {}
STICKER_CACHE_TTL = 3600 # 1 hour

# Initialize the main router
router = Router()
ai_service = get_ai_service()
gatekeeper = get_gatekeeper()
tts_service = get_tts_service()
transcription_service = get_transcription_service()
config = Config()

async def trigger_summarization_if_needed(chat_context: ChatContext, gatekeeper):
    """History Optimization: If history exceeds 20 messages, summarize the oldest ones"""
    if len(chat_context.history) > 20:
        logger.info(f"History length is {len(chat_context.history)}. Triggering summarization.")
        # Keep the last 5 messages, summarize the rest
        messages_to_summarize = chat_context.history[:-5]
        messages_to_keep = chat_context.history[-5:]
        
        summary = await gatekeeper.summarize_history(messages_to_summarize)
        
        # Replace history with the summary + the kept messages
        new_history = [{"role": "user", "text": f"[SYSTEM: CONTEXT SUMMARY OF PREVIOUS CHAT]\n{summary}"}] + messages_to_keep
        await chat_context.replace_history(new_history)

async def _process_bot_turn(message: Message, chat_context: ChatContext, text: str, media: dict = None, db_text: str = None):
    """Handles the core logic of calling the AI and sending the response for any message type."""
    # 1. Gatekeeper determines if a response is needed
    action = await gatekeeper.decide(text, chat_context)

    if action == GatekeeperAction.IGNORE:
        return

    # 2. Proceed with Persona response
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        # Update user in DB with latest metadata dynamically
        await chat_context._db.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

        # Get avatar description (cached or live using Gemini Vision)
        avatar_desc = await AvatarService.get_and_describe_avatar(
            bot=message.bot,
            user_id=message.from_user.id,
            db_manager=chat_context._db
        )

        # Build sender_info context dictionary
        sender_info = {
            "first_name": message.from_user.first_name,
            "last_name": message.from_user.last_name,
            "username": message.from_user.username,
            "language_code": message.from_user.language_code,
            "avatar_description": avatar_desc
        }

        # Generate Response
        response_text, tool_calls = await ai_service.generate_response(text, chat_context, media, sender_info)

    db_response_text = ""
    bot_msg_to_save = None

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
            emotion = call.args.get("emotion", "happy").lower()
            logger.info(f"LLM requested sending a sticker with emotion: {emotion}")
            
            emotion_emoji_map = {
                "happy": ["😊", "🙂", "😁", "😄", "🥰", "😸", "😺"],
                "sad": ["😢", "😭", "😔", "☹️", "🥺", "😿"],
                "love": ["❤️", "🥰", "😍", "😘", "😻"],
                "angry": ["😠", "😡", "🤬", "😾"],
                "laughing": ["😂", "🤣", "😆", "😸"],
                "surprised": ["😮", "😱", "😳", "🤯", "🙀"],
                "cool": ["😎", "😏", "😼"],
            }
            
            target_emojis = emotion_emoji_map.get(emotion, ["😊", "🥰", "🙂"])
            
            # Fetch sticker set from settings
            settings = await chat_context._db.get_system_settings()
            sticker_set_name = settings.get("sticker_set_name") or "Animals"
            
            sent_msg = None
            try:
                # Check cache first
                current_time = time.time()
                cached_data = sticker_cache.get(sticker_set_name)
                
                if not cached_data or current_time - cached_data['timestamp'] > STICKER_CACHE_TTL:
                    sticker_set = await message.bot.get_sticker_set(name=sticker_set_name)
                    if sticker_set and sticker_set.stickers:
                        sticker_cache[sticker_set_name] = {
                            'stickers': sticker_set.stickers,
                            'timestamp': current_time
                        }
                
                cached_stickers = sticker_cache.get(sticker_set_name, {}).get('stickers', [])
                
                if cached_stickers:
                    matching_sticker = None
                    for sticker in cached_stickers:
                        if sticker.emoji:
                            clean_sticker_emoji = sticker.emoji.replace("\ufe0f", "")
                            clean_targets = [te.replace("\ufe0f", "") for te in target_emojis]
                            if clean_sticker_emoji in clean_targets:
                                matching_sticker = sticker
                                break
                    
                    if not matching_sticker:
                        matching_sticker = cached_stickers[0]
                        
                    if matching_sticker:
                        sent_msg = await message.answer_sticker(sticker=matching_sticker.file_id)
            except Exception as se:
                logger.error(f"Failed to send sticker from set {sticker_set_name}: {se}")
                
            if not sent_msg:
                # Fallback to plain emoji
                fallback_emoji = target_emojis[0] if target_emojis else "😊"
                sent_msg = await message.answer(fallback_emoji)
                
            sticker_action = f"*(Отправила стикер с эмоцией {emotion})*"
            db_response_text = db_response_text + f"\n{sticker_action}" if db_response_text else sticker_action
            bot_msg_to_save = sent_msg
            
        elif call.name == ToolName.SEND_VOICE.value:
            text_to_speak = call.args.get("text_to_speak", "")
            
            async with ChatActionSender.record_voice(bot=message.bot, chat_id=message.chat.id):
                # Generate the voice using ElevenLabs
                audio_bytes = await tts_service.generate_voice(text_to_speak)
            
            if audio_bytes:
                voice_file = BufferedInputFile(audio_bytes, filename="voice.ogg")
                sent_msg = await message.answer_voice(voice=voice_file)
            else:
                # Fallback to text if TTS fails or isn't configured
                sent_msg = await message.answer(f"*(Голосовое сообщение)*: {text_to_speak}", parse_mode="Markdown")
                
            db_response_text = f"🎤 [Голосовое]: {text_to_speak}"
            bot_msg_to_save = sent_msg
            response_text = ""

    # Save User Message to DB via the Context abstraction.
    # Use db_text if provided (for media), otherwise use actual text.
    # Enrich with timestamp.
    msg_timestamp = message.date.strftime("%H:%M") if message.date else None
    await chat_context.add_message(
        "user",
        db_text if db_text else text,
        message.message_id,
        timestamp=msg_timestamp,
        reactions=None
    )

    # Send text response if the model provided one
    if response_text:
        # Split by paragraphs (\n\n) to avoid breaking markdown in lists or code blocks
        parts = [p.strip() for p in response_text.split('\n\n') if p.strip()]
        
        explicit_reply_id = getattr(message, 'reply_to_message_id', None)
        
        for i, part in enumerate(parts):
            # Only reply to the specific message for the first part to avoid notification spam
            if explicit_reply_id and i == 0:
                bot_message = await message.reply(
                    part,
                    reply_to_message_id=explicit_reply_id
                )
            elif chat_context.is_group and i == 0:
                bot_message = await message.reply(part)
            else:
                bot_message = await message.answer(part)
                
            # Save Bot Message to DB
            await chat_context.add_message("model", part, bot_message.message_id)
            
            # Brief pause between messages so they appear in correct order
            if i < len(parts) - 1:
                await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
                await asyncio.sleep(1.0)
                
    if db_response_text and bot_msg_to_save:
        # Save Voice or Sticker action to DB
        await chat_context.add_message("model", db_response_text.strip(), bot_msg_to_save.message_id)

    # History Optimization
    await trigger_summarization_if_needed(chat_context, gatekeeper)

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
    
    # Detect language
    lang = message.from_user.language_code or 'en'
    
    # Generate a more human-like, persona-driven greeting based on language
    if lang.startswith('ru'):
        if chat_context.is_group:
            text = f"Привет всем в {name}! Я Мия. Буду рада пообщаться, если понадоблюсь. )"
        else:
            text = f"Привет, {name}! Я Мия. Рада познакомиться. Рассказывай, что у тебя интересного, или просто давай поболтаем. )"
    elif lang.startswith('uk'):
        if chat_context.is_group:
            text = f"Привіт усім у {name}! Я Мія. Буду рада поспілкуватися, якщо знадоблюсь. )"
        else:
            text = f"Привіт, {name}! Я Мія. Рада познайомитися. Розповідай, що в тебе цікавого, або просто давай поспілкуємося. )"
    else:
        if chat_context.is_group:
            text = f"Hi everyone in {name}! I'm Mia. I'll be glad to chat if you need me. )"
        else:
            text = f"Hi, {name}! I'm Mia. Nice to meet you. Tell me what's interesting with you, or let's just chat. )"
            
    await message.answer(text)

@router.message(filters.Command("clear"))
async def clear_command(message: Message, chat_context: ChatContext):
    await chat_context.clear_history()
    await message.reply("Context history has been cleared.")

@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_message(message: Message, chat_context: ChatContext):
    if chat_context.is_disabled or not chat_context.responds_to("text"):
        return
    await _process_bot_turn(message, chat_context, text=message.text)

@router.message(F.photo | F.video | F.document | F.voice | F.video_note | F.sticker)
async def handle_media_message(message: Message, chat_context: ChatContext):
    logger.info(f"Entered handle_media_message for update with content_type: {message.content_type}")
        
    media = None
    file_id = None
    file_size = 0
    mime_type = ""
    media_type_name = ""
    
    # Identify media type
    if message.photo:
        # Telegram sends multiple sizes. The last one is the largest.
        photo = message.photo[-1]
        file_id = photo.file_id
        file_size = photo.file_size
        mime_type = "image/jpeg"
        media_type_name = "photo"
    elif message.video:
        file_id = message.video.file_id
        file_size = message.video.file_size
        mime_type = message.video.mime_type or "video/mp4"
        media_type_name = "video"
    elif message.voice:
        file_id = message.voice.file_id
        file_size = message.voice.file_size
        mime_type = message.voice.mime_type or "audio/ogg"
        media_type_name = "voice message"
    elif message.video_note:
        file_id = message.video_note.file_id
        file_size = message.video_note.file_size
        mime_type = "video/mp4"
        media_type_name = "video note"
    elif message.document and message.document.mime_type.startswith('image/'):
        file_id = message.document.file_id
        file_size = message.document.file_size
        mime_type = message.document.mime_type
        media_type_name = "image document"
    else:
        # Ignore unsupported documents or stickers for AI analysis
        # But we still record that they sent something in the context
        db_text = f"*(User sent a {message.content_type})*"
        if message.caption:
            db_text += f"\nCaption: {message.caption}"
        await chat_context.add_message("user", db_text, message.message_id)
        await trigger_summarization_if_needed(chat_context, gatekeeper)
        return

    # Process media
    try:
        if mime_type.startswith('image/'):
            media_bytes = await MediaService.process_image(message.bot, file_id, file_size)
            if media_bytes:
                media = {"mime_type": mime_type, "data": media_bytes}
                # Overwrite mime_type to jpeg since we compressed it
                media["mime_type"] = "image/jpeg"
        else:
            media_bytes = await MediaService.process_audio_video(message.bot, file_id, file_size)
            if media_bytes:
                if message.voice and transcription_service.is_configured:
                    transcription = await transcription_service.transcribe_audio(media_bytes)
                    if transcription:
                        lang = message.from_user.language_code or 'en'
                        if lang.startswith('ru'):
                            prefix = "🎤 [Голосовое]: "
                        elif lang.startswith('uk'):
                            prefix = "🎤 [Голосове]: "
                        else:
                            prefix = "🎤 [Voice]: "
                        
                        transcribed_text = f"{prefix}{transcription}"
                        await _process_bot_turn(message, chat_context, text=transcribed_text, media=None, db_text=transcribed_text)
                        return
                
                media = {"mime_type": mime_type, "data": media_bytes}
    except Exception as e:
        logger.error(f"Error processing media: {e}")
        media = None
        
    text = message.caption or ""
    
    if not media:
        if file_size > 4.5 * 1024 * 1024:
             await message.reply("Этот файл слишком большой, я не могу его сейчас обработать. (Ограничение: 4.5 МБ)")
        return
        
    # How we store this interaction in the DB (text only, to save space!)
    db_text = f"*(User sent a {media_type_name})*"
    if text:
        db_text += f"\nCaption: {text}"
        
    await _process_bot_turn(message, chat_context, text=text, media=media, db_text=db_text)

@router.message_reaction()
async def handle_message_reaction(event: MessageReactionUpdated, chat_context: ChatContext):
    """Handle reaction updates on messages and sync them to history."""
    emojis = []
    for r in event.new_reaction:
        emoji = getattr(r, "emoji", None)
        if emoji:
            emojis.append(emoji)
        else:
            custom_id = getattr(r, "custom_emoji_id", None)
            if custom_id:
                emojis.append("⭐️")
                
    await chat_context.update_message_reactions(event.message_id, emojis if emojis else None)
    logger.debug(f"Updated reactions for message {event.message_id} in chat {chat_context.id}: {emojis}")
