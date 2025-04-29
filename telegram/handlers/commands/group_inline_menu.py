import logging
from aiogram import Router, filters
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from database.models import User
from database.dao import GroupDAO
from ..utils import is_user_group_admin
from ..utils import get_group_or_none
from .keyboards import get_settings_keyboard, get_group_settings_keyboard

logger = logging.getLogger(__name__)
router = Router()

@router.callback_query(lambda c: c.data == "show_group_help")
async def show_group_help_callback(callback: CallbackQuery, group_dao: GroupDAO):
    """Показать справку по настройкам группы."""
    chat = callback.message.chat
    group = await get_group_or_none(group_dao, chat)
    help_text = (
        "<b>Довідка по груповим налаштуванням:</b>\n\n"
        "• <b>Глобальні відповіді</b> — Увімкнення/вимкнення всіх відповідей бота у групі.\n\n"
        "• <b>Відповіді на текст</b> — Бот буде відповідати на текстові повідомлення у групі.\n\n"
        "• <b>Відповіді на голосові</b> — Бот буде відповідати на голосові повідомлення у групі.\n\n"
        "• <b>Відповіді на фото</b> — Бот буде відповідати на фото у групі.\n\n"
        "• <b>Відповіді на відео-повідомлення</b> — Бот буде відповідати на відео-кружки у групі.\n\n"
        "• <b>Транскрипція голосових</b> — Бот буде перетворювати голосові у текст.\n\n"
        "• <b>Транскрипція відео-кружків</b> — Бот буде перетворювати відео-кружки у текст.\n\n"
        "\nНатисніть на кнопку, щоб змінити відповідне налаштування."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Закрити довідку", callback_data="close_group_help")
    ]])
    await callback.message.edit_text(help_text, parse_mode="HTML", reply_markup=keyboard)

@router.callback_query(lambda c: c.data == "close_group_help")
async def close_group_help_callback(callback: CallbackQuery, group_dao: GroupDAO):
    chat = callback.message.chat
    group = await get_group_or_none(group_dao, chat)
    is_admin = await is_user_group_admin(chat, callback.from_user.id)
    keyboard = get_group_settings_keyboard(group, show_user_settings_button=is_admin)
    await callback.message.edit_text(
        "<b>Налаштування групи</b>\n\nКеруйте налаштуваннями групи за допомогою кнопок нижче:",
        parse_mode="HTML",
        reply_markup=keyboard
    )

logger = logging.getLogger(__name__)
router = Router()

from aiogram.types import CallbackQuery

# Ensure get_group_settings_keyboard is defined before usage in callbacks

@router.callback_query(lambda c: c.data == "toggle_group_global_disabled")
async def toggle_group_global_disabled_callback(callback: CallbackQuery, group_dao: GroupDAO):
    chat = callback.message.chat

    is_admin = await is_user_group_admin(chat, callback.from_user.id)
    if not is_admin:
        await callback.answer("Тільки адміністратор може змінювати налаштування групи", show_alert=True)
        return
    group = await get_group_or_none(group_dao, chat)
    if not group:
        await callback.answer("Групу не знайдено у базі", show_alert=True)
        return
    new_value = not group.is_global_disabled
    success = await group_dao.update_group_settings(group_id=group.id, is_global_disabled=new_value)
    if success:
        group.is_global_disabled = new_value
        status = "увімкнено" if not new_value else "вимкнено"
        await callback.answer(f"✅ Глобальні відповіді {status}")
        await callback.message.edit_reply_markup(reply_markup=get_group_settings_keyboard(group, show_user_settings_button=is_admin))
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(lambda c: c.data == "toggle_group_responds_to_text")
async def toggle_group_responds_to_text_callback(callback: CallbackQuery, group_dao: GroupDAO):
    chat = callback.message.chat
    
    is_admin = await is_user_group_admin(chat, callback.from_user.id)
    if not is_admin:
        await callback.answer("Тільки адміністратор може змінювати налаштування групи", show_alert=True)
        return
    group = await get_group_or_none(group_dao, chat)
    if not group:
        await callback.answer("Групу не знайдено у базі", show_alert=True)
        return
    new_value = not group.responds_to_text
    success = await group_dao.update_group_settings(group_id=group.id, responds_to_text=new_value)
    if success:
        group.responds_to_text = new_value
        status = "увімкнено" if new_value else "вимкнено"
        await callback.answer(f"✅ Відповіді на текст {status}")
        await callback.message.edit_reply_markup(reply_markup=get_group_settings_keyboard(group, show_user_settings_button=is_admin))
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(lambda c: c.data == "toggle_group_responds_to_voice")
async def toggle_group_responds_to_voice_callback(callback: CallbackQuery, group_dao: GroupDAO):
    chat = callback.message.chat
    is_admin = await is_user_group_admin(chat, callback.from_user.id)
    if not is_admin:
        await callback.answer("Тільки адміністратор може змінювати налаштування групи", show_alert=True)
        return
    group = await get_group_or_none(group_dao, chat)
    if not group:
        await callback.answer("Групу не знайдено у базі", show_alert=True)
        return
    new_value = not group.responds_to_voice
    success = await group_dao.update_group_settings(group_id=group.id, responds_to_voice=new_value)
    if success:
        group.responds_to_voice = new_value
        status = "увімкнено" if new_value else "вимкнено"
        await callback.answer(f"✅ Відповіді на голосові {status}")
        await callback.message.edit_reply_markup(reply_markup=get_group_settings_keyboard(group, show_user_settings_button=is_admin))
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(lambda c: c.data == "toggle_group_responds_to_photo")
async def toggle_group_responds_to_photo_callback(callback: CallbackQuery, group_dao: GroupDAO):
    chat = callback.message.chat
    is_admin = await is_user_group_admin(chat, callback.from_user.id)
    if not is_admin:
        await callback.answer("Тільки адміністратор може змінювати налаштування групи", show_alert=True)
        return
    group = await get_group_or_none(group_dao, chat)
    if not group:
        await callback.answer("Групу не знайдено у базі", show_alert=True)
        return
    new_value = not group.responds_to_photo
    success = await group_dao.update_group_settings(group_id=group.id, responds_to_photo=new_value)
    if success:
        group.responds_to_photo = new_value
        status = "увімкнено" if new_value else "вимкнено"
        await callback.answer(f"✅ Відповіді на фото {status}")
        await callback.message.edit_reply_markup(reply_markup=get_group_settings_keyboard(group, show_user_settings_button=is_admin))
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(lambda c: c.data == "toggle_group_responds_to_video_note")
async def toggle_group_responds_to_video_note_callback(callback: CallbackQuery, group_dao: GroupDAO):
    chat = callback.message.chat
    is_admin = await is_user_group_admin(chat, callback.from_user.id)
    if not is_admin:
        await callback.answer("Тільки адміністратор може змінювати налаштування групи", show_alert=True)
        return
    group = await get_group_or_none(group_dao, chat)
    if not group:
        await callback.answer("Групу не знайдено у базі", show_alert=True)
        return
    new_value = not group.responds_to_video_note
    success = await group_dao.update_group_settings(group_id=group.id, responds_to_video_note=new_value)
    if success:
        group.responds_to_video_note = new_value
        status = "увімкнено" if new_value else "вимкнено"
        await callback.answer(f"✅ Відповіді на відео-повідомлення {status}")
        await callback.message.edit_reply_markup(reply_markup=get_group_settings_keyboard(group, show_user_settings_button=is_admin))
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(lambda c: c.data == "toggle_group_transcribe_voice_only")
async def toggle_group_transcribe_voice_only_callback(callback: CallbackQuery, group_dao: GroupDAO):
    chat = callback.message.chat
    is_admin = await is_user_group_admin(chat, callback.from_user.id)
    if not is_admin:
        await callback.answer("Тільки адміністратор може змінювати налаштування групи", show_alert=True)
        return
    group = await get_group_or_none(group_dao, chat)
    if not group:
        await callback.answer("Групу не знайдено у базі", show_alert=True)
        return
    new_value = not group.transcribe_voice_only
    success = await group_dao.update_group_settings(group_id=group.id, transcribe_voice_only=new_value)
    if success:
        group.transcribe_voice_only = new_value
        status = "увімкнено" if new_value else "вимкнено"
        await callback.answer(f"✅ Транскрипція тільки голосових {status}")
        await callback.message.edit_reply_markup(reply_markup=get_group_settings_keyboard(group, show_user_settings_button=is_admin))
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(lambda c: c.data == "toggle_group_transcribe_video_note")
async def toggle_group_transcribe_video_note_callback(callback: CallbackQuery, group_dao: GroupDAO):
    chat = callback.message.chat
    is_admin = await is_user_group_admin(chat, callback.from_user.id)
    if not is_admin:
        await callback.answer("Тільки адміністратор може змінювати налаштування групи", show_alert=True)
        return
    group = await get_group_or_none(group_dao, chat)
    if not group:
        await callback.answer("Групу не знайдено у базі", show_alert=True)
        return
    new_value = not group.transcribe_video_note
    success = await group_dao.update_group_settings(group_id=group.id, transcribe_video_note=new_value)
    if success:
        group.transcribe_video_note = new_value
        status = "увімкнено" if new_value else "вимкнено"
        await callback.answer(f"✅ Транскрипція відео-кружків {status}")
        await callback.message.edit_reply_markup(reply_markup=get_group_settings_keyboard(group, show_user_settings_button=is_admin))
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(lambda c: c.data == "refresh_group_menu")
async def refresh_group_menu_callback(callback: CallbackQuery, group_dao: GroupDAO):
    """Обновляет меню группы (inline keyboard) по кнопке 'Оновити'."""
    chat = callback.message.chat
    is_admin = await is_user_group_admin(chat, callback.from_user.id)
    if not is_admin:
        await callback.answer("Тільки адміністратор може змінювати налаштування групи", show_alert=True)
        return
    group = await get_group_or_none(group_dao, chat)
    if not group:
        await callback.answer("Групу не знайдено у базі", show_alert=True)
        return
    try:
        keyboard = get_group_settings_keyboard(group, show_user_settings_button=is_admin)
        await callback.message.edit_reply_markup(reply_markup=keyboard)
        await callback.answer("Меню оновлено")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer("Меню вже актуальне")
        else:
            logger.error(f"Error refreshing group menu: {e}")
            await callback.answer("❌ Помилка при оновленні меню", show_alert=True)

def get_group_settings_keyboard(group, show_user_settings_button=False) -> InlineKeyboardMarkup:
    """Создает клавиатуру с настройками для группы."""
    keyboard = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if not group.is_global_disabled else '❌'} Відповідати на повідомлення",
                callback_data="toggle_group_global_disabled"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{'✅' if group.responds_to_text else '❌'} Відповідати на текст",
                callback_data="toggle_group_responds_to_text"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{'✅' if group.responds_to_voice else '❌'} Відповідати на голосові",
                callback_data="toggle_group_responds_to_voice"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{'✅' if group.responds_to_photo else '❌'} Відповідати на фото",
                callback_data="toggle_group_responds_to_photo"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{'✅' if group.responds_to_video_note else '❌'} Відповідати на відео-повідомлення",
                callback_data="toggle_group_responds_to_video_note"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{'✅' if group.transcribe_voice_only else '❌'} Транскрибувати тільки голосові",
                callback_data="toggle_group_transcribe_voice_only"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{'✅' if group.transcribe_video_note else '❌'} Транскрибувати відео-кружки",
                callback_data="toggle_group_transcribe_video_note"
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
    # Кнопка перехода к пользовательским настройкам для админов и владельцев
    if show_user_settings_button:
        keyboard.append([
            InlineKeyboardButton(
                text="👤 Мої налаштування",
                callback_data="back_to_user_settings"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@router.callback_query(lambda c: c.data == "open_group_settings_menu")
async def open_group_settings_menu_callback(callback: CallbackQuery, group_dao: GroupDAO, user: User):
    
    chat = callback.message.chat
    is_admin = await is_user_group_admin(chat, callback.from_user.id)
    group = await get_group_or_none(group_dao, chat)
    if not group:
        await callback.answer("Групу не знайдено у базі", show_alert=True)
        return
    
    keyboard = get_group_settings_keyboard(group, show_user_settings_button=is_admin)
    await callback.message.edit_text(
        "👥 <b>Налаштування групи</b>\n\nКеруйте груповими параметрами бота:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(lambda c: c.data == "back_to_user_settings")
async def back_to_user_settings_callback(callback: CallbackQuery, user: User):
    
    keyboard = get_settings_keyboard(user)
    await callback.message.edit_text(
        "🎛 <b>Головне меню</b>\n\nКеруйте налаштуваннями бота за допомогою кнопок нижче:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

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
