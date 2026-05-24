import logging
import asyncio
from aiogram import Router, filters, F
from aiogram.types import Message, BufferedInputFile, MessageReactionUpdated, ReplyParameters
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
import random
from services.avatar_service import AvatarService

logger = logging.getLogger(__name__)

# Simple in-memory cache for sticker sets to prevent Telegram API rate limits
sticker_cache = {}
STICKER_CACHE_TTL = 3600 # 1 hour

# Burst handling structures
burst_timers = {}
burst_queues = {}

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

async def _enqueue_bot_turn(message: Message, chat_context: ChatContext, text: str, media: dict = None, db_text: str = None):
    """Enqueues the message into a burst buffer. Executes only when the user stops typing for a brief moment."""
    # 0.1 Prepend username and forward info so the bot knows who is talking and if it's a forward
    sender_name = message.from_user.first_name if message.from_user else "Unknown"
    
    original_sender = None
    if getattr(message, "forward_origin", None):
        origin = message.forward_origin
        if origin.type == "user":
            original_sender = origin.sender_user.first_name
        elif origin.type == "hidden_user":
            original_sender = origin.sender_user_name
        elif origin.type == "chat":
            original_sender = origin.sender_chat.title
        elif origin.type == "channel":
            original_sender = origin.chat.title
            
    if original_sender:
        if chat_context.is_group:
            prefix_tag = f"[{sender_name} (forwarded from {original_sender})]: "
        else:
            prefix_tag = f"[(Forwarded from {original_sender})]: "
    else:
        prefix_tag = f"[{sender_name}]: " if chat_context.is_group else ""
    
    if text:
        text = f"{prefix_tag}{text}"
    elif prefix_tag:
        text = prefix_tag.strip()
        
    if db_text:
        db_text = f"{prefix_tag}{db_text}"
    elif prefix_tag:
        db_text = text
    
    # 0.2 Enrich text with reply/quote context
    if message.reply_to_message:
        replied_msg = message.reply_to_message
        replied_user = replied_msg.from_user.first_name if replied_msg.from_user else "Unknown"
        if replied_msg.from_user and replied_msg.from_user.id == message.bot.id:
            replied_user = "Mia"
            
        content = replied_msg.text or replied_msg.caption or f"[{replied_msg.content_type}]"
        if len(content) > 100:
            content = content[:97] + "..."
            
        prefix = ""
        if getattr(message, "quote", None):
            quote_text = message.quote.text
            if len(quote_text) > 100:
                quote_text = quote_text[:97] + "..."
            prefix = f"*[В ответ на {replied_user} (цитата: \"{quote_text}\")]*\n"
        else:
            prefix = f"*[В ответ на {replied_user}: \"{content}\"]*\n"
            
        text = f"{prefix}{text}" if text else prefix.strip()
        if db_text:
            db_text = f"{prefix}{db_text}"
        else:
            db_text = text

    # Add to Burst Queue
    chat_id = message.chat.id
    current_time = time.time()
    burst_timers[chat_id] = current_time
    
    if chat_id not in burst_queues:
        burst_queues[chat_id] = {"messages": [], "texts": [], "db_texts": [], "media_list": []}
        
    burst = burst_queues[chat_id]
    burst["messages"].append(message)
    if text:
        burst["texts"].append(text)
    if db_text:
        burst["db_texts"].append(db_text)
    if media:
        burst["media_list"].append(media)
        
    # Wait to see if more messages arrive in this burst
    await asyncio.sleep(3.0)
    
    # If the timer has moved forward, another message arrived. Let it handle the execution.
    if burst_timers.get(chat_id) != current_time:
        return
        
    # I am the last message in the burst. Time to execute!
    final_burst = burst_queues.pop(chat_id, None)
    if not final_burst:
        return
        
    combined_text = "\n\n".join(final_burst["texts"])
    combined_db_text = "\n\n".join(final_burst["db_texts"])
    media_list = final_burst["media_list"]
    last_message = final_burst["messages"][-1]
    
    # Force saving the ignored combined text into DB if Gatekeeper ignores it
    # But wait, we want to save it to DB unconditionally first!
    msg_timestamp = last_message.date.strftime("%H:%M") if last_message.date else None
    
    # Unconditionally save to DB so history is fully maintained
    if combined_db_text:
        await chat_context.add_message(
            "user",
            combined_db_text,
            last_message.message_id,
            timestamp=msg_timestamp,
            reactions=None
        )
        
    # Now that it's in the DB, Gatekeeper can see it either in history or in current text
    # Gatekeeper check
    action = await gatekeeper.decide(combined_text, chat_context)
    
    if action == GatekeeperAction.IGNORE:
        return

    # Proceed with Persona response
    async with ChatActionSender.typing(bot=last_message.bot, chat_id=last_message.chat.id):
        # Update user in DB with latest metadata dynamically
        await chat_context._db.get_or_create_user(
            telegram_id=last_message.from_user.id,
            username=last_message.from_user.username,
            first_name=last_message.from_user.first_name,
            last_name=last_message.from_user.last_name
        )

        # Get avatar description
        avatar_desc = await AvatarService.get_and_describe_avatar(
            bot=last_message.bot,
            user_id=last_message.from_user.id,
            db_manager=chat_context._db
        )

        chat_title = last_message.chat.title if chat_context.is_group else "Private Chat"
        sender_info = {
            "user_id": last_message.from_user.id,
            "first_name": last_message.from_user.first_name,
            "last_name": last_message.from_user.last_name,
            "username": last_message.from_user.username,
            "language_code": last_message.from_user.language_code,
            "avatar_description": avatar_desc,
            "bot": last_message.bot,
            "chat_id": last_message.chat.id,
            "chat_title": chat_title
        }

        # Generate Response (pass the media list!)
        response_text, tool_calls = await ai_service.generate_response(combined_text, chat_context, media_list, sender_info)

    db_response_text = ""
    bot_msg_to_save = None
    
    # Store requested reply parameters locally (Message object is frozen)
    requested_reply_id = None
    requested_reply_quote = ""

    from core.engine.tool_executor import ToolExecutorService
    db_response_text, bot_msg_to_save, requested_reply_id, requested_reply_quote, response_text = await ToolExecutorService.execute_local_tools(
        last_message, chat_context, tool_calls, response_text
    )

    if response_text:
        import re
        import html
        # Clean up literal "\n" strings that the model sometimes outputs by mistake
        response_text = response_text.replace("\\n", "\n")
        # Normalize excessive newlines (3 or more) into exactly two (\n\n)
        response_text = re.sub(r'\n{3,}', '\n\n', response_text)
        
        # Telegram HTML parser is very strict. It breaks on raw '<' or '>' signs that aren't valid tags (like <b>, <i>, <code>).
        # We need to escape '<' and '>' that are used in normal text or math, but preserve legitimate markdown/html if possible.
        # Since Gemini natively outputs markdown, we either need a proper markdown-to-html converter, or we strip/escape bad tags.
        # For safety against "Unsupported start tag", we will escape `<` and `>` unless they are part of supported HTML tags.
        supported_tags = ['b', 'strong', 'i', 'em', 'u', 'ins', 's', 'strike', 'del', 'span', 'tg-spoiler', 'a', 'code', 'pre', 'tg-emoji']
        
        # A simple approach to protect rogue '<' signs is to replace them with &lt;
        # A more robust fix for this specific aiogram/telegram issue when using parse_mode="HTML" is to just use a fallback mechanism
        
        parts = [p.strip() for p in response_text.split('\n\n') if p.strip()]
        
        for i, part in enumerate(parts):
            bot_message = None
            try:
                if requested_reply_id and i == 0:
                    reply_params = ReplyParameters(message_id=requested_reply_id)
                    if requested_reply_quote:
                        reply_params.quote = requested_reply_quote
                        
                    bot_message = await last_message.bot.send_message(
                        chat_id=last_message.chat.id,
                        text=part,
                        reply_parameters=reply_params
                    )
                elif chat_context.is_group and i == 0:
                    bot_message = await last_message.reply(part)
                else:
                    bot_message = await last_message.answer(part)
            except Exception as e:
                logger.warning(f"Failed to send message chunk due to formatting error, retrying safely: {e}")
                # Fallback: strip HTML/Markdown tags and send as plain text
                safe_part = html.escape(part)
                
                if requested_reply_id and i == 0:
                    reply_params = ReplyParameters(message_id=requested_reply_id)
                    if requested_reply_quote:
                        reply_params.quote = requested_reply_quote
                        
                    bot_message = await last_message.bot.send_message(
                        chat_id=last_message.chat.id,
                        text=safe_part,
                        reply_parameters=reply_params,
                        parse_mode=None
                    )
                elif chat_context.is_group and i == 0:
                    bot_message = await last_message.reply(safe_part, parse_mode=None)
                else:
                    bot_message = await last_message.answer(safe_part, parse_mode=None)
                
            if bot_message:
                await chat_context.add_message("model", part, bot_message.message_id)
            
            if i < len(parts) - 1:
                await last_message.bot.send_chat_action(chat_id=last_message.chat.id, action="typing")
                await asyncio.sleep(1.0)
                
    if db_response_text and bot_msg_to_save:
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
    await _enqueue_bot_turn(message, chat_context, text=message.text)

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
        # Telegram sends multiple sizes sorted by quality. The last one is the highest quality.
        # Standard photos are heavily compressed by Telegram (usually < 1MB), so it's safe to take the last one.
        best_photo = message.photo[-1]
        
        file_id = best_photo.file_id
        file_size = best_photo.file_size or 0
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
    elif message.sticker:
        from services.sticker_service import StickerService
        from core.key_manager import get_key_manager
        
        # Analyze and cache the user's sticker on the fly
        desc = await StickerService.analyze_single_sticker(
            message.bot, 
            chat_context._db, 
            get_key_manager(), 
            message.sticker
        )
        db_text = f"*(Пользователь отправил стикер: {desc})*"
        
        # Pass to bot logic in case we want it to react immediately to the sticker itself
        # This will also handle adding it to the DB with the correct username prefix
        await _enqueue_bot_turn(message, chat_context, text="", media=None, db_text=db_text)
        return
    else:
        # Ignore unsupported documents for AI analysis
        # But we still record that they sent something in the context
        db_text = f"*(User sent a {message.content_type})*"
        if message.caption:
            db_text += f"\nCaption: {message.caption}"
        
        await _enqueue_bot_turn(message, chat_context, text="", media=None, db_text=db_text)
        return

    # Process media
    text = ""
    try:
        video_desc = ""
        if mime_type.startswith('image/'):
            media_bytes = await MediaService.process_image(message.bot, file_id, file_size)
            if media_bytes:
                media = {"mime_type": mime_type, "data": media_bytes}
                # Overwrite mime_type to jpeg since we compressed it
                media["mime_type"] = "image/jpeg"
        else:
            media_bytes = await MediaService.process_audio_video(message.bot, file_id, file_size)
            if media_bytes:
                if (message.voice or message.video_note or message.video) and transcription_service.is_configured:
                    transcription = await transcription_service.transcribe_audio(media_bytes)
                    if transcription:
                        lang = message.from_user.language_code or 'en'
                        prefix = "🎤 [Голосовое]: " if lang.startswith('ru') else "🎤 [Голосове]: " if lang.startswith('uk') else "🎤 [Voice]: "
                        text = (text + f"\n{prefix}{transcription}").strip()
                        
                if message.voice:
                    # Voice has no visual component for Gemini
                    if not text: 
                        text = "🎤 [Пустое голосовое]" # Fallback
                    await _enqueue_bot_turn(message, chat_context, text=text, media=None, db_text=text)
                    return
                
                # Get visual description for video notes to save in history
                if message.video_note:
                    from services.sticker_service import StickerService
                    from core.key_manager import get_key_manager
                    video_desc = await StickerService.analyze_video_note(message.bot, get_key_manager(), file_id)
                
                media = {"mime_type": mime_type, "data": media_bytes}
    except Exception as e:
        logger.error(f"Error processing media: {e}")
        media = None
        video_desc = ""
        
    text = message.caption or text or ""
    
    if not media:
        if file_size > 4.5 * 1024 * 1024:
             # Pass the failure to the LLM so it can contextually apologize
             text = "[SYSTEM: The user attempted to send a media file, but it was over the 4.5MB limit. Please inform the user playfully that the file is too large for you to process.]"
             db_text = f"*(User attempted to send a {media_type_name} but it was too large)*"
             await _enqueue_bot_turn(message, chat_context, text=text, media=None, db_text=db_text)
        return
        
    # How we store this interaction in the DB (text only, to save space!)
    db_text = f"*(User sent a {media_type_name}. FileID: {file_id}"
    if video_desc:
        db_text += f". Visuals: {video_desc}"
    db_text += ")*"
    
    if text:
        db_text += f"\nCaption/Audio: {text}"
        
    await _enqueue_bot_turn(message, chat_context, text=text, media=media, db_text=db_text)

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
