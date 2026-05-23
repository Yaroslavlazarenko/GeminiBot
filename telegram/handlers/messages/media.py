import logging
from aiogram import Router, F
from aiogram.types import Message
from typing import Dict, Any

logger = logging.getLogger(__name__)
router = Router()

@router.message(F.photo | F.video | F.document | F.voice | F.video_note | F.sticker)
async def handle_media_message(
    message: Message, 
    user: Dict[str, Any],
    group: Dict[str, Any] = None,
    is_group: bool = False
):
    # Check settings
    settings = group.get("settings", {}) if is_group else user.get("settings", {})
    if settings.get("is_global_disabled", False):
        return
        
    await message.reply("Media processing is currently being upgraded for the new architecture. It will be available shortly!")
