import logging
import asyncio
import os
import time
import random
from typing import List, Tuple, Optional
from aiogram.types import Message, BufferedInputFile, FSInputFile
from aiogram.utils.chat_action import ChatActionSender
from google.genai.types import FunctionCall

from core.database import ChatContext
from core.enums import ToolName
from services.tts_service import get_tts_service

logger = logging.getLogger(__name__)

# Simple in-memory cache for sticker sets to prevent Telegram API rate limits
sticker_cache = {}
STICKER_CACHE_TTL = 3600 # 1 hour

# Telegram only allows these emojis as reactions (Bot API ReactionTypeEmoji)
# Source: https://core.telegram.org/bots/api#reactiontypeemoji
VALID_TELEGRAM_REACTIONS = {
    "👍", "👎", "❤", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱", "🤬", "😢", "🎉",
    "🤩", "🤮", "💩", "🙏", "👌", "🕊", "🤡", "🥱", "🥴", "😍", "🐳", "❤‍🔥",
    "🌚", "🌭", "💯", "🤣", "⚡", "🍌", "🏆", "💔", "🤨", "😐", "🍓", "🍾",
    "💋", "🖕", "😈", "😴", "😭", "🤓", "👻", "👨‍💻", "👀", "🎃", "🙈", "😇",
    "😨", "🤝", "✍", "🤗", "🫡", "🎅", "🎄", "☃", "💅", "🤪", "🗿", "🆒",
    "💘", "🙉", "🦄", "😘", "💊", "🙊", "😎", "👾", "🤷‍♂", "🤷", "🤷‍♀", "😡",
}

class ToolExecutorService:
    @staticmethod
    async def execute_local_tools(
        last_message: Message, 
        chat_context: ChatContext, 
        tool_calls: List[FunctionCall],
        response_text: str
    ) -> Tuple[str, Message, int, str, str]:
        """
        Executes local Telegram tools directly from the orchestrator.
        Returns a tuple: (db_response_text, bot_msg_to_save, requested_reply_id, requested_reply_quote, response_text)
        """
        db_response_text = ""
        bot_msg_to_save = None
        requested_reply_id = None
        requested_reply_quote = ""
        
        tts_service = get_tts_service()
        
        for call in tool_calls:
            logger.info(f"LLM called tool: {call.name} with args {call.args}")
            args = call.args if isinstance(call.args, dict) else {}
                
            if call.name == ToolName.ADD_REACTION.value:
                emoji = args.get("emoji")
                message_ids = args.get("message_ids", [last_message.message_id])
                if not message_ids:
                    message_ids = [last_message.message_id]

                # Validate emoji against Telegram's allowed reactions list
                if emoji and emoji not in VALID_TELEGRAM_REACTIONS:
                    logger.warning(f"Skipping invalid Telegram reaction emoji: {emoji}")
                elif emoji:
                    for m_id in message_ids:
                        try:
                            await last_message.bot.set_message_reaction(
                                chat_id=last_message.chat.id,
                                message_id=int(m_id),
                                reaction=[{"type": "emoji", "emoji": emoji}]
                            )
                        except Exception as e:
                            logger.error(f"Failed to add reaction {emoji} to {m_id}: {e}")
                        
            elif call.name == ToolName.REPLY_TO_MESSAGE.value:
                requested_reply_id = int(args.get("message_id", 0)) if args.get("message_id") else None
                requested_reply_quote = args.get("quote") or ""

            elif call.name == ToolName.EDIT_MESSAGE.value:
                target_msg_id = int(args.get("message_id", 0)) if args.get("message_id") else None
                new_text = args.get("new_text") or ""
                if target_msg_id and new_text:
                    try:
                        # 1. Edit the message in Telegram via Bot API
                        await last_message.bot.edit_message_text(
                            chat_id=last_message.chat.id,
                            message_id=target_msg_id,
                            text=new_text
                        )
                        # 2. Update message in DB & in-memory context tracking what changed
                        old_text = await chat_context.edit_bot_message(target_msg_id, new_text)
                        logger.info(f"Successfully edited bot message {target_msg_id} in chat {last_message.chat.id}. Old: {repr(old_text[:60]) if old_text else 'unknown'} -> New: {repr(new_text[:60])}")
                    except Exception as edit_err:
                        logger.error(f"Failed to edit message {target_msg_id} in Telegram: {edit_err}")
                
            elif call.name == ToolName.SEND_SPECIFIC_STICKER.value:
                sticker_id = args.get("sticker_id") or ""
                logger.info(f"LLM requested sending a specific sticker with ID: {sticker_id}")
                
                sent_msg = None
                try:
                    sticker_data = await chat_context._db.stickers.find_one({"_id": sticker_id})
                    if sticker_data and sticker_data.get("file_id"):
                        sent_msg = await last_message.answer_sticker(sticker=sticker_data["file_id"])
                    if not sent_msg:
                        try:
                            sent_msg = await last_message.answer("😊")
                        except Exception:
                            pass
                except Exception as e:
                    logger.error(f"Failed to send specific sticker: {e}")
                    try:
                        sent_msg = await last_message.answer("😊")
                    except Exception:
                        pass
                    
                sticker_action = f"*(Отправила специфический стикер)*"
                db_response_text = db_response_text + f"\n{sticker_action}" if db_response_text else sticker_action
                bot_msg_to_save = sent_msg

            elif call.name == ToolName.SEND_STICKER.value:
                emotion = (args.get("emotion") or "happy").lower()
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
                
                settings = await chat_context._db.get_system_settings()
                sticker_sets_raw = settings.get("sticker_set_names") or settings.get("sticker_set_name") or "Animals"
                sticker_sets = [s.strip() for s in sticker_sets_raw.split(',') if s.strip()]
                if not sticker_sets:
                    sticker_sets = ["Animals"]
                
                sent_msg = None
                matching_stickers = []
                first_available_sticker = None
                
                for set_name in sticker_sets:
                    try:
                        current_time = time.time()
                        cached_data = sticker_cache.get(set_name)
                        
                        if not cached_data or current_time - cached_data['timestamp'] > STICKER_CACHE_TTL:
                            sticker_set = await last_message.bot.get_sticker_set(name=set_name)
                            if sticker_set and sticker_set.stickers:
                                sticker_cache[set_name] = {
                                    'stickers': sticker_set.stickers,
                                    'timestamp': current_time
                                }
                        
                        cached_stickers = sticker_cache.get(set_name, {}).get('stickers', [])
                        
                        if cached_stickers and not first_available_sticker:
                            first_available_sticker = cached_stickers[0]
                            
                        for sticker in cached_stickers:
                            if sticker.emoji:
                                clean_sticker_emoji = sticker.emoji.replace("\ufe0f", "")
                                clean_targets = [te.replace("\ufe0f", "") for te in target_emojis]
                                if clean_sticker_emoji in clean_targets:
                                    matching_stickers.append(sticker)
                    except Exception as se:
                        logger.error(f"Failed to fetch sticker set {set_name}: {se}")

                try:
                    chosen_sticker = random.choice(matching_stickers) if matching_stickers else first_available_sticker
                    if chosen_sticker:
                        sent_msg = await last_message.answer_sticker(sticker=chosen_sticker.file_id)
                except Exception as e:
                    logger.error(f"Failed to send sticker: {e}")
                    
                if not sent_msg:
                    try:
                        fallback_emoji = target_emojis[0] if target_emojis else "😊"
                        sent_msg = await last_message.answer(fallback_emoji)
                    except Exception:
                        pass
                    
                sticker_action = f"*(Отправила стикер с эмоцией {emotion})*"
                db_response_text = db_response_text + f"\n{sticker_action}" if db_response_text else sticker_action
                bot_msg_to_save = sent_msg
                
            elif call.name == ToolName.SEND_VOICE.value:
                text_to_speak = args.get("text_to_speak") or ""
                audio_bytes = args.get("_audio_bytes")
                
                if audio_bytes:
                    try:
                        async with ChatActionSender.record_voice(bot=last_message.bot, chat_id=last_message.chat.id):
                            voice_file = BufferedInputFile(audio_bytes, filename="voice.ogg")
                            sent_msg = await last_message.answer_voice(voice=voice_file)
                        
                        db_response_text = f"🎤 [Голосовое]: {text_to_speak}"
                    except Exception as e:
                        logger.error(f"Failed to send voice message: {e}")
                bot_msg_to_save = sent_msg
                response_text = ""

            elif call.name == ToolName.GENERATE_IMAGE.value:
                image_bytes = args.get("_image_bytes")
                prompt = args.get("prompt") or ""
                custom_caption = args.get("caption") or ""
                sent_msg = None
                if image_bytes:
                    try:
                        async with ChatActionSender.upload_photo(bot=last_message.bot, chat_id=last_message.chat.id):
                            photo_file = BufferedInputFile(image_bytes, filename="image.jpg")
                            
                            # Deduplicate caption: if custom_caption is set and matches response_text, clear response_text
                            caption = custom_caption or None
                            if not caption and response_text and len(response_text) <= 1024:
                                # Short response_text becomes the photo caption cleanly
                                caption = response_text
                                response_text = ""
                            elif custom_caption and response_text:
                                # If response_text is just an exact or near duplicate of the caption, don't double send it
                                if response_text.strip().lower() == custom_caption.strip().lower() or response_text.strip() in custom_caption:
                                    response_text = ""
                                # Otherwise, response_text is a genuine long follow-up story/continuation — keep it so it sends after the photo!
                                
                            sent_msg = await last_message.answer_photo(photo=photo_file, caption=caption)
                        db_response_text = f"🎨 [Сгенерировано изображение]: {prompt}"
                    except Exception as e:
                        logger.error(f"Failed to send generated photo: {e}")
                bot_msg_to_save = sent_msg

            elif call.name == ToolName.SEND_MUSIC.value:
                track_id = args.get("track_id") or ""
                query = args.get("query") or ""
                custom_caption = args.get("caption") or ""
                audio_url = args.get("_audio_url")
                audio_file = args.get("_audio_file")
                audio_bytes = args.get("_audio_bytes")
                title = args.get("_title") or query or "Music"
                performer = args.get("_performer") or "Track"
                sent_msg = None

                audio_input = None
                if audio_file and os.path.exists(audio_file):
                    from aiogram.types import FSInputFile
                    clean_filename = f"{performer} - {title}.mp3".replace("/", "_")
                    audio_input = FSInputFile(audio_file, filename=clean_filename)
                elif audio_bytes:
                    clean_filename = f"{performer} - {title}.mp3".replace("/", "_")
                    audio_input = BufferedInputFile(audio_bytes, filename=clean_filename)
                elif audio_url:
                    audio_input = audio_url

                if audio_input:
                    try:
                        async with ChatActionSender.upload_voice(bot=last_message.bot, chat_id=last_message.chat.id):
                            caption = custom_caption or None
                            sent_msg = await last_message.answer_audio(
                                audio=audio_input,
                                title=title,
                                performer=performer,
                                caption=caption
                            )
                        action_str = f"🎵 [Музыка]: {performer} - {title}"
                        db_response_text = db_response_text + f"\n{action_str}" if db_response_text else action_str
                    except Exception as e:
                        logger.error(f"Failed to send music audio: {e}")
                bot_msg_to_save = sent_msg

            elif call.name == ToolName.SEND_WIDGET.value:
                html_code = args.get("html") or ""
                custom_caption = args.get("caption") or ""
                height = args.get("height")
                width = args.get("width")
                sent_msg = None
                if html_code:
                    try:
                        async with ChatActionSender.choose_sticker(bot=last_message.bot, chat_id=last_message.chat.id):
                            caption = custom_caption or (response_text if len(response_text) <= 1024 else None)
                            if caption and response_text and (response_text.strip() in caption or caption in response_text):
                                response_text = ""
                            
                            # Sanitize HTML to prevent Telegram Widget layout breakage:
                            # 1. Replace height: 100vh or any XXvh with normal relative heights
                            import re
                            html_code = re.sub(r'height\s*:\s*\d+vh\s*;?', 'min-height: auto;', html_code)
                            html_code = re.sub(r'width\s*:\s*\d+vw\s*;?', 'width: 100%;', html_code)

                            # Invoke Telegram Bot API 10.4 sendWidget via custom session call
                            params = {
                                "chat_id": last_message.chat.id,
                                "html": html_code,
                            }
                            if caption:
                                params["caption"] = caption
                            # Prefer auto-sizing (None) unless specifically bounded
                            if height and int(height) > 0:
                                params["height"] = int(height)
                            if width and int(width) > 0:
                                params["width"] = int(width)
                            
                            from aiogram.methods.base import TelegramMethod
                            class SendWidgetMethod(TelegramMethod[Message]):
                                __returning__ = Message
                                __api_method__ = "sendWidget"
                                chat_id: int | str
                                html: str
                                caption: Optional[str] = None
                                height: Optional[int] = None
                                width: Optional[int] = None
                            
                            method = SendWidgetMethod(**params)
                            sent_msg = await last_message.bot(method)
                            db_response_text = f"📱 [Интерактивный виджет]: {caption or 'HTML Widget'}"
                            logger.info(f"Successfully sent Telegram HTML widget to chat {last_message.chat.id}")
                    except Exception as e:
                        logger.error(f"Failed to send Telegram widget: {e}")
                bot_msg_to_save = sent_msg

            elif call.name == ToolName.EDIT_WIDGET.value:
                target_msg_id = int(args.get("message_id", 0)) if args.get("message_id") else None
                chat_data = args.get("chat_data") or ""
                if target_msg_id and chat_data:
                    try:
                        from aiogram.methods.base import TelegramMethod
                        class EditMessageWidgetDataMethod(TelegramMethod[bool]):
                            __returning__ = bool
                            __api_method__ = "editMessageWidgetData"
                            chat_id: int | str
                            message_id: int
                            chat_data: str
                        
                        method = EditMessageWidgetDataMethod(
                            chat_id=last_message.chat.id,
                            message_id=target_msg_id,
                            chat_data=chat_data
                        )
                        await last_message.bot(method)
                        logger.info(f"Successfully updated widget data for message {target_msg_id}")
                    except Exception as edit_err:
                        logger.error(f"Failed to edit widget data for message {target_msg_id}: {edit_err}")

        return db_response_text, bot_msg_to_save, requested_reply_id, requested_reply_quote, response_text