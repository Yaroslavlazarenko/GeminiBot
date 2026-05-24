import logging
import asyncio
import time
import random
from typing import List, Tuple
from aiogram.types import Message, BufferedInputFile
from aiogram.utils.chat_action import ChatActionSender
from google.genai.types import FunctionCall

from core.database import ChatContext
from core.enums import ToolName
from services.tts_service import get_tts_service

logger = logging.getLogger(__name__)

# Simple in-memory cache for sticker sets to prevent Telegram API rate limits
sticker_cache = {}
STICKER_CACHE_TTL = 3600 # 1 hour

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
                
            if call.name == ToolName.ADD_REACTION.value:
                emoji = call.args.get("emoji")
                message_ids = call.args.get("message_ids", [last_message.message_id])
                if not message_ids:
                    message_ids = [last_message.message_id]
                    
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
                requested_reply_id = int(call.args.get("message_id", 0)) if call.args.get("message_id") else None
                requested_reply_quote = call.args.get("quote", "")
                
            elif call.name == ToolName.SEND_SPECIFIC_STICKER.value:
                sticker_id = call.args.get("sticker_id", "")
                logger.info(f"LLM requested sending a specific sticker with ID: {sticker_id}")
                
                sent_msg = None
                try:
                    sticker_data = await chat_context._db.stickers.find_one({"_id": sticker_id})
                    if sticker_data and sticker_data.get("file_id"):
                        sent_msg = await last_message.answer_sticker(sticker=sticker_data["file_id"])
                    if not sent_msg:
                        sent_msg = await last_message.answer("😊")
                except Exception as e:
                    logger.error(f"Failed to send specific sticker: {e}")
                    sent_msg = await last_message.answer("😊")
                    
                sticker_action = f"*(Отправила специфический стикер)*"
                db_response_text = db_response_text + f"\n{sticker_action}" if db_response_text else sticker_action
                bot_msg_to_save = sent_msg

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
                    fallback_emoji = target_emojis[0] if target_emojis else "😊"
                    sent_msg = await last_message.answer(fallback_emoji)
                    
                sticker_action = f"*(Отправила стикер с эмоцией {emotion})*"
                db_response_text = db_response_text + f"\n{sticker_action}" if db_response_text else sticker_action
                bot_msg_to_save = sent_msg
                
            elif call.name == ToolName.SEND_VOICE.value:
                text_to_speak = call.args.get("text_to_speak", "")
                audio_bytes = call.args.get("_audio_bytes")
                
                if audio_bytes:
                    async with ChatActionSender.record_voice(bot=last_message.bot, chat_id=last_message.chat.id):
                        voice_file = BufferedInputFile(audio_bytes, filename="voice.ogg")
                        sent_msg = await last_message.answer_voice(voice=voice_file)
                    
                    db_response_text = f"🎤 [Голосовое]: {text_to_speak}"
                bot_msg_to_save = sent_msg
                response_text = ""
                
        return db_response_text, bot_msg_to_save, requested_reply_id, requested_reply_quote, response_text