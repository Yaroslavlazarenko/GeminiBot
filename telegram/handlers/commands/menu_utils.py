"""Shared menu utilities for both personal and group menus."""
from aiogram.types import Message, InlineKeyboardMarkup
from database.models import User
from ..utils import rate_limited_edit

def get_user_menu_text(user: User) -> str:
    """Формує текст меню користувача з актуальним статусом і кількістю активних налаштувань."""
    active_settings = sum([
        not user.is_global_disabled,
        user.responds_to_text,
        user.responds_to_voice,
        user.responds_to_photo,
        user.responds_to_video_note
    ])
    
    return (
        f"👤 <b>Мої налаштування</b>\n"
        f"{'🟢' if not user.is_global_disabled else '🔴'} Загальний статус: {'увімкнено' if not user.is_global_disabled else 'вимкнено'}\n"
        f"📊 Активно налаштувань: {active_settings}/5\n\n"
        "Використовуйте кнопки нижче для керування:"
    )

def get_group_menu_text(group) -> str:
    """Формує текст меню групи з актуальним статусом і кількістю активних налаштувань."""
    active_settings = sum([
        not group.is_global_disabled,
        group.responds_to_text,
        group.responds_to_voice,
        group.responds_to_photo,
        group.responds_to_video_note
    ])
    
    return (
        f"👥 <b>Налаштування групи</b>\n"
        f"{'🟢' if not group.is_global_disabled else '🔴'} Загальний статус: {'увімкнено' if not group.is_global_disabled else 'вимкнено'}\n"
        f"📊 Активно налаштувань: {active_settings}/5\n\n"
        "Використовуйте кнопки нижче для керування:"
    )

async def refresh_user_menu(message: Message, user: User, is_admin: bool = False, keyboard: InlineKeyboardMarkup = None):
    """Оновлює текст і клавіатуру меню користувача."""
    await rate_limited_edit(
        message,
        text=get_user_menu_text(user),
        reply_markup=keyboard,
        parse_mode="HTML"
    )

async def refresh_group_menu(message: Message, group, keyboard: InlineKeyboardMarkup = None):
    """Оновлює текст і клавіатуру меню групи."""
    await message.edit_text(
        text=get_group_menu_text(group),
        reply_markup=keyboard,
        parse_mode="HTML"
    )