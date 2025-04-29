import logging
from aiogram import Router, filters
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest

from database.models import User
from database.dao import GroupDAO
from ..utils import get_group_or_none

logger = logging.getLogger(__name__)
router = Router()

def get_group_settings_keyboard(group) -> InlineKeyboardMarkup:
    """Создает клавиатуру с настройками для группы."""
    keyboard = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if group.responds_to_text else '❌'} Відповідати на текст",
                callback_data="toggle_group_responds_to_text"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔄 Оновити",
                callback_data="refresh_group_menu"
            ),
            InlineKeyboardButton(
                text="❌ Закрити",
                callback_data="close_menu"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@router.message(filters.Command("menu"))
async def show_group_menu(message: Message, group_dao: GroupDAO):
    """Обработчик /menu в группе: показывает настройки группы."""
    if message.chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return
    group = await get_group_or_none(group_dao, message.chat)
    if not group:
        await message.answer("Група не знайдена у базі. Відправте будь-яке повідомлення у групу, щоб зареєструвати її.")
        return
    keyboard = get_group_settings_keyboard(group)
    await message.answer(
        "👥 <b>Налаштування групи</b>\n\nКеруйте груповими параметрами бота:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
