"""Shared menu utilities for both personal and group menus."""
from aiogram.types import Message, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest
from database.models import User, Group
from ..utils import rate_limited_edit

def get_user_menu_text(user: User) -> str:
    """Generate user settings menu text."""
    global_status = "🟢 Увімкнено" if not user.is_global_disabled else "🔴 Вимкнено"
    menu_text = f"<b>🛠 Налаштування користувача</b>\n\nГлобальні відповіді: {global_status}\n"
    
    if not user.is_global_disabled:
        text_status = "✅ Увімкнено" if user.responds_to_text else "❌ Вимкнено"
        voice_status = "✅ Увімкнено" if user.responds_to_voice else "❌ Вимкнено"
        photo_status = "✅ Увімкнено" if user.responds_to_photo else "❌ Вимкнено"
        video_note_status = "✅ Увімкнено" if user.responds_to_video_note else "❌ Вимкнено"
        sticker_status = "✅ Увімкнено" if user.responds_to_sticker else "❌ Вимкнено"
        
        menu_text += (
            f"\nВідповіді на текст: {text_status}"
            f"\nВідповіді на голос: {voice_status}"
            f"\nВідповіді на фото: {photo_status}"
            f"\nВідповіді на відео-кружки: {video_note_status}"
            f"\nВідповіді на стікери: {sticker_status}"
        )
        
        if user.responds_to_voice:
            voice_mode = "📝 Транскрипція" if user.transcribe_voice_only else "🎤 Відповідь"
            menu_text += f"\nРежим голосових: {voice_mode}"
            
        if user.responds_to_video_note:
            video_mode = "📝 Транскрипція" if user.transcribe_video_note else "🎥 Відповідь"
            menu_text += f"\nРежим відео: {video_mode}"
            
    return menu_text

def get_group_menu_text(group: Group) -> str:
    """Generate group settings menu text."""
    global_status = "🟢 Увімкнено" if not group.is_global_disabled else "🔴 Вимкнено"
    menu_text = f"<b>⚙️ Налаштування групи</b>\n\nГрупа: {group.name}\nГлобальні відповіді: {global_status}\n"
    
    if not group.is_global_disabled:
        text_status = "✅ Увімкнено" if group.responds_to_text else "❌ Вимкнено"
        voice_status = "✅ Увімкнено" if group.responds_to_voice else "❌ Вимкнено"
        photo_status = "✅ Увімкнено" if group.responds_to_photo else "❌ Вимкнено"
        video_note_status = "✅ Увімкнено" if group.responds_to_video_note else "❌ Вимкнено"
        sticker_status = "✅ Увімкнено" if group.responds_to_sticker else "❌ Вимкнено"
        
        menu_text += (
            f"\nВідповіді на текст: {text_status}"
            f"\nВідповіді на голос: {voice_status}"
            f"\nВідповіді на фото: {photo_status}"
            f"\nВідповіді на відео-кружки: {video_note_status}"
            f"\nВідповіді на стікери: {sticker_status}"
        )
        
        if group.responds_to_voice:
            voice_mode = "📝 Транскрипція" if group.transcribe_voice_only else "🎤 Відповідь"
            menu_text += f"\nРежим голосових: {voice_mode}"
            
        if group.responds_to_video_note:
            video_mode = "📝 Транскрипція" if group.transcribe_video_note else "🎥 Відповідь"
            menu_text += f"\nРежим відео: {video_mode}"
            
    return menu_text

async def refresh_user_menu(message: Message, user: User, is_admin: bool, keyboard) -> None:
    """Refresh user menu text and keyboard."""
    try:
        await message.edit_text(get_user_menu_text(user), reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

async def refresh_group_menu(message: Message, group: Group, keyboard) -> None:
    """Refresh group menu text and keyboard."""
    try:
        await message.edit_text(get_group_menu_text(group), reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise