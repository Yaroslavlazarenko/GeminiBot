import logging
import asyncio
import httpx
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
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
    ODESSA_TZ = ZoneInfo("Europe/Kyiv")
except Exception:
    ODESSA_TZ = timezone(timedelta(hours=3))

def _format_odessa_time(dt: datetime) -> str:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ODESSA_TZ).strftime("%H:%M")
from services.avatar_service import AvatarService

logger = logging.getLogger(__name__)

# Simple in-memory cache for sticker sets to prevent Telegram API rate limits
sticker_cache = {}
STICKER_CACHE_TTL = 3600 # 1 hour

# Burst handling structures
burst_timers = {}
burst_queues = {}

# Generation lock per chat — prevents concurrent AI generation for the same chat
generation_locks = {}   # chat_id -> asyncio.Lock

def _get_generation_lock(chat_id: int) -> asyncio.Lock:
    """Return (creating if needed) the asyncio.Lock for a given chat_id."""
    if chat_id not in generation_locks:
        generation_locks[chat_id] = asyncio.Lock()
    return generation_locks[chat_id]

# Initialize the main router
router = Router()
ai_service = get_ai_service()
gatekeeper = get_gatekeeper()
tts_service = get_tts_service()
transcription_service = get_transcription_service()
config = Config()

MAX_EPOCHS = 4
HISTORY_TRIGGER = 150
LIVE_WINDOW = 30

async def trigger_summarization_if_needed(chat_context: ChatContext, gatekeeper):
    """History Optimization: Hierarchical epoch-based summarization.

    Instead of compressing everything into one blob, creates structured "epochs"
    with metadata (time range, participants, topics). Maintains up to MAX_EPOCHS
    epoch summaries plus a live window of recent messages.

    Target context budget: 100k tokens total.
    ~15-20k for system prompt + tools + world memory.
    ~10k for current turn.
    ~70k available for history ≈ 150 live messages + epoch summaries.
    Keep last LIVE_WINDOW messages intact for recent context quality.
    """
    # Separate live messages from epoch entries
    live_messages = [m for m in chat_context.history if not m.get("is_epoch")]
    existing_epochs = [m for m in chat_context.history if m.get("is_epoch")]

    if len(live_messages) <= HISTORY_TRIGGER:
        return

    logger.info(f"History has {len(live_messages)} live messages and {len(existing_epochs)} epochs. Triggering epoch summarization.")

    messages_to_summarize = live_messages[:-LIVE_WINDOW]
    messages_to_keep = live_messages[-LIVE_WINDOW:]

    # Epoch rollup: merge the two oldest epochs if we're at the limit
    if len(existing_epochs) >= MAX_EPOCHS:
        oldest_two = existing_epochs[:2]
        logger.info(f"Merging epochs {oldest_two[0].get('epoch_index', '?')} and {oldest_two[1].get('epoch_index', '?')}")
        merged_text = await gatekeeper.merge_epochs([e["text"] for e in oldest_two])

        merged_epoch = {
            "role": "system",
            "is_epoch": True,
            "epoch_index": oldest_two[0].get("epoch_index", 0),
            "epoch_start_date": oldest_two[0].get("epoch_start_date"),
            "epoch_end_date": oldest_two[1].get("epoch_end_date"),
            "participants": list(set(
                oldest_two[0].get("participants", []) +
                oldest_two[1].get("participants", [])
            )),
            "topics": list(set(
                oldest_two[0].get("topics", []) +
                oldest_two[1].get("topics", [])
            )),
            "message_count": (
                oldest_two[0].get("message_count", 0) +
                oldest_two[1].get("message_count", 0)
            ),
            "text": f"[MERGED CONTEXT EPOCHS]\n{merged_text}"
        }
        existing_epochs = [merged_epoch] + existing_epochs[2:]

    # Generate structured epoch summary
    epoch_summary = await gatekeeper.summarize_history_structured(messages_to_summarize)

    # Determine epoch index
    if existing_epochs:
        next_index = max(e.get("epoch_index", 0) for e in existing_epochs) + 1
    else:
        next_index = 1

    # Extract date range from messages
    first_ts = messages_to_summarize[0].get("timestamp", "?")
    last_ts = messages_to_summarize[-1].get("timestamp", "?")

    today_str = datetime.now(ODESSA_TZ).strftime("%Y-%m-%d")

    # Build the epoch entry
    topics_str = ", ".join(epoch_summary.topics) if epoch_summary.topics else "general conversation"
    new_epoch = {
        "role": "system",
        "is_epoch": True,
        "epoch_index": next_index,
        "epoch_start_date": today_str,
        "epoch_end_date": today_str,
        "participants": epoch_summary.participants or [],
        "topics": epoch_summary.topics or [],
        "message_count": len(messages_to_summarize),
        "text": f"[CONTEXT EPOCH {next_index} | Topics: {topics_str}]\n{epoch_summary.summary}"
    }

    new_history = existing_epochs + [new_epoch] + messages_to_keep
    await chat_context.replace_history(new_history)
    logger.info(f"Created epoch {next_index} from {len(messages_to_summarize)} messages. "
                f"History now: {len(existing_epochs)+1} epochs + {len(messages_to_keep)} live messages.")

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

    # Track user activity — resets proactive "awaiting_reply" flag
    try:
        await chat_context._db.mark_chat_activity(chat_id, chat_context.is_group)
    except Exception as e:
        logger.error(f"Failed to mark chat activity: {e}")
        
    combined_text = "\n\n".join(final_burst["texts"])
    combined_db_text = "\n\n".join(final_burst["db_texts"])
    media_list = final_burst["media_list"]
    last_message = final_burst["messages"][-1]

    msg_timestamp = _format_odessa_time(last_message.date)
    
    # Acquire per-chat generation lock — prevents concurrent AI generation for the same chat.
    # While the lock is held, new messages for this chat will accumulate in burst_queues
    # and be processed after the current generation finishes (with the bot's response already in history).
    lock = _get_generation_lock(chat_id)
    async with lock:
        # Gatekeeper check (BEFORE saving to history, so the message doesn't appear
        # both in history and as the "new message" in the prompt — that caused duplicates)
        if chat_context.is_group:
            action = await gatekeeper.decide(combined_text, chat_context)
        else:
            action = GatekeeperAction.RESPOND

        if action == GatekeeperAction.IGNORE:
            # Save user message to DB even when ignored, to keep history complete
            if combined_db_text:
                await chat_context.add_message(
                    "user",
                    combined_db_text,
                    last_message.message_id,
                    timestamp=msg_timestamp,
                    reactions=None
                )
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
                "chat_title": chat_title,
                "message_id": last_message.message_id,
                "timestamp": msg_timestamp
            }

            # Save user message BEFORE generation (crash-safe), but marked pending
            # so _convert_history_to_gemini() skips it (preventing duplication)
            if combined_db_text:
                await chat_context.add_message(
                    "user",
                    combined_db_text,
                    last_message.message_id,
                    timestamp=msg_timestamp,
                    reactions=None,
                    pending=True
                )

            # Generate Response — the pending message won't appear in Gemini history
            response_text, tool_calls = await ai_service.generate_response(combined_text, chat_context, media_list, sender_info)

            # Confirm the pending message — it's now safe to appear in future history conversions
            if combined_db_text:
                chat_context.confirm_message(last_message.message_id)

        logger.info(f"AI returned: response_text={repr(response_text[:200]) if response_text else 'EMPTY'}, tool_calls={len(tool_calls)}")

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
                        try:
                            if requested_reply_quote:
                                reply_params = ReplyParameters(message_id=int(requested_reply_id), quote=requested_reply_quote)
                            else:
                                reply_params = ReplyParameters(message_id=int(requested_reply_id))

                            bot_message = await last_message.bot.send_message(
                                chat_id=last_message.chat.id,
                                text=part,
                                reply_parameters=reply_params
                            )
                        except Exception as reply_err:
                            logger.warning(f"Failed to send with reply_to {requested_reply_id}, falling back to normal reply: {reply_err}")
                            requested_reply_id = None  # Don't retry with broken reply_id
                            if chat_context.is_group:
                                bot_message = await last_message.reply(part)
                            else:
                                bot_message = await last_message.answer(part)
                    elif chat_context.is_group and i == 0:
                        bot_message = await last_message.reply(part)
                    else:
                        bot_message = await last_message.answer(part)
                except Exception as e:
                    logger.warning(f"Failed to send message chunk due to formatting error, retrying safely: {e}")
                    # Fallback: strip HTML/Markdown tags and send as plain text
                    safe_part = html.escape(part)

                    try:
                        if chat_context.is_group and i == 0:
                            bot_message = await last_message.reply(safe_part, parse_mode=None)
                        else:
                            bot_message = await last_message.answer(safe_part, parse_mode=None)
                    except Exception as e2:
                        logger.error(f"Failed to send even plain text fallback: {e2}")

                if bot_message:
                    await chat_context.add_message("model", part, bot_message.message_id)

                if i < len(parts) - 1:
                    await last_message.bot.send_chat_action(chat_id=last_message.chat.id, action="typing")
                    await asyncio.sleep(1.0)

        if db_response_text and bot_msg_to_save:
            await chat_context.add_message("model", db_response_text.strip(), bot_msg_to_save.message_id)

        # History Optimization
        await trigger_summarization_if_needed(chat_context, gatekeeper)

async def _get_server_ip() -> str:
    """Get the external IP address of the server."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("https://api.ipify.org")
            return resp.text.strip()
    except Exception:
        return None

@router.message(filters.Command("admin"))
async def admin_command(message: Message, chat_context: ChatContext):
    # Check if the user is the authorized admin
    if message.from_user.id != config.admin_telegram_id:
        return

    token = create_admin_session()
    server_ip = await _get_server_ip()

    if server_ip:
        url = f"http://{server_ip}:{config.admin_port}/?token={token}"
        text = (
            f"🔐 <b>Admin Panel Access</b>\n\n"
            f"Here is your temporary, secure access link:\n\n"
            f'<a href="{url}">{url}</a>'
        )
    else:
        text = (
            f"🔐 <b>Admin Panel Access</b>\n\n"
            f"Could not detect server IP. Open manually:\n\n"
            f"<code>http://&lt;YOUR_SERVER_IP&gt;:{config.admin_port}/?token={token}</code>"
        )
    await message.answer(text, parse_mode="HTML")

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

            # If video is too large for Gemini but we can still extract audio
            elif message.video and file_size > MediaService.get_max_media_size() and transcription_service.is_configured:
                logger.info(f"Video too large for Gemini ({file_size} bytes), extracting audio track...")
                audio_bytes = await MediaService.extract_audio_from_video(message.bot, file_id, file_size)
                if audio_bytes:
                    transcription = await transcription_service.transcribe_audio(audio_bytes)
                    if transcription:
                        text = f"[SYSTEM: Пользователь отправил видео, но оно слишком большое и ты не можешь его увидеть. Однако удалось извлечь аудиодорожку. Скажи пользователю что видео ты не увидела, но звук услышала. Вот транскрипция аудио:]\n🎤 [Аудио из видео]: {transcription}"
                        db_text = f"*(Пользователь отправил большое видео. FileID: {file_id}. Извлечена аудиодорожка)*\n🎤 [Аудио]: {transcription}"
                        if message.caption:
                            text += f"\nCaption: {message.caption}"
                            db_text += f"\nCaption: {message.caption}"
                        await _enqueue_bot_turn(message, chat_context, text=text, media=None, db_text=db_text)
                        return
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
