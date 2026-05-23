import logging
from aiogram import Router, F
from aiogram.types import Message
from database.manager import ChatContext

logger = logging.getLogger(__name__)
router = Router()

@router.message(F.photo | F.video | F.document | F.voice | F.video_note | F.sticker)
async def handle_media_message(message: Message, chat_context: ChatContext):
    
    if chat_context.is_disabled:
        return
        
    await message.reply("Media processing is currently being upgraded for the new architecture. It will be available shortly!")
