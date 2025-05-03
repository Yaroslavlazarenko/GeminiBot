import logging
from aiogram import Router, filters, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from database.models import User
from database.dao import GroupDAO, MessageHistoryDAO
from ..utils import get_group_or_none, is_user_group_admin
from .keyboards import get_settings_keyboard, get_group_settings_keyboard, get_group_clear_menu_keyboard
from .menu_utils import refresh_user_menu, refresh_group_menu, get_group_menu_text

logger = logging.getLogger(__name__)
router = Router()

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
    await message.answer(get_group_menu_text(group), reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data == "toggle_group_global_disabled")
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
        keyboard = get_group_settings_keyboard(group, show_user_settings_button=is_admin)
        await refresh_group_menu(callback.message, group, keyboard)
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(F.data == "toggle_group_responds_to_text")
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
        keyboard = get_group_settings_keyboard(group, show_user_settings_button=is_admin)
        await refresh_group_menu(callback.message, group, keyboard)
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(F.data == "toggle_group_responds_to_voice")
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
        keyboard = get_group_settings_keyboard(group, show_user_settings_button=is_admin)
        await refresh_group_menu(callback.message, group, keyboard)
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(F.data == "toggle_group_responds_to_photo")
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
        keyboard = get_group_settings_keyboard(group, show_user_settings_button=is_admin)
        await refresh_group_menu(callback.message, group, keyboard)
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(F.data == "toggle_group_responds_to_video_note")
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
        keyboard = get_group_settings_keyboard(group, show_user_settings_button=is_admin)
        await refresh_group_menu(callback.message, group, keyboard)
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(F.data == "toggle_group_transcribe_voice_only")
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
        keyboard = get_group_settings_keyboard(group, show_user_settings_button=is_admin)
        await refresh_group_menu(callback.message, group, keyboard)
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(F.data == "toggle_group_transcribe_video_note")
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
        keyboard = get_group_settings_keyboard(group, show_user_settings_button=is_admin)
        await refresh_group_menu(callback.message, group, keyboard)
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(F.data == "refresh_group_menu")
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
        await refresh_group_menu(callback.message, group, keyboard)
        await callback.answer("Меню оновлено")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer("Меню вже актуальне")
        else:
            logger.error(f"Error refreshing group menu: {e}")
            await callback.answer("❌ Помилка при оновленні меню", show_alert=True)

@router.callback_query(F.data == "open_group_settings_menu")
async def open_group_settings_menu_callback(callback: CallbackQuery, group_dao: GroupDAO, user: User):
    chat = callback.message.chat
    is_admin = await is_user_group_admin(chat, callback.from_user.id)
    group = await get_group_or_none(group_dao, chat)
    if not group:
        await callback.answer("Групу не знайдено у базі", show_alert=True)
        return

    keyboard = get_group_settings_keyboard(group, show_user_settings_button=is_admin)
    await refresh_group_menu(callback.message, group, keyboard)
    await callback.answer()

@router.callback_query(F.data == "back_to_user_settings")
async def back_to_user_settings_callback(callback: CallbackQuery, user: User):
    chat = callback.message.chat
    is_admin = await is_user_group_admin(chat, callback.from_user.id)
    await refresh_user_menu(callback.message, user, is_admin)
    await callback.answer()

@router.callback_query(F.data == "show_group_help")
async def show_group_help_callback(callback: CallbackQuery):
    """Показать справку по настройкам группы."""
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
    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Закрити довідку", callback_data="close_group_help")
    ]])
    await callback.message.edit_text(
        help_text,
        parse_mode="HTML",
        reply_markup=keyboard
    )

@router.callback_query(F.data == "close_group_help")
async def close_group_help_callback(callback: CallbackQuery, group_dao: GroupDAO):
    chat = callback.message.chat
    group = await get_group_or_none(group_dao, chat)
    is_admin = await is_user_group_admin(chat, callback.from_user.id)
    keyboard = get_group_settings_keyboard(group, show_user_settings_button=is_admin)
    await refresh_group_menu(callback.message, group, keyboard)

@router.callback_query(F.data == "clear_group_messages")
async def clear_group_messages_callback(callback: CallbackQuery, group_dao: GroupDAO):
    """Show group clear messages submenu."""
    chat = callback.message.chat
    is_admin = await is_user_group_admin(chat, callback.from_user.id)
    if not is_admin:
        await callback.answer("Тільки адміністратор може очищати історію групи", show_alert=True)
        return

    group = await get_group_or_none(group_dao, chat)
    if not group:
        await callback.answer("Групу не знайдено у базі", show_alert=True)
        return

    await callback.message.edit_text(
        "🗑 <b>Очищення історії групи</b>\n\nОберіть кількість повідомлень для видалення:",
        reply_markup=get_group_clear_menu_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("clear_group_messages_"))
async def handle_clear_group_messages(callback: CallbackQuery, group_dao: GroupDAO, message_dao: MessageHistoryDAO):
    """Handle specific group message clearing options."""
    chat = callback.message.chat
    is_admin = await is_user_group_admin(chat, callback.from_user.id)
    if not is_admin:
        await callback.answer("Тільки адміністратор може очищати історію групи", show_alert=True)
        return

    group = await get_group_or_none(group_dao, chat)
    if not group:
        await callback.answer("Групу не знайдено у базі", show_alert=True)
        return
    
    try:
        option = callback.data.split("_")[-1]
        limit = None if option == "all" else int(option)
        deleted_count = await message_dao.clear_history(
            group_id=group.id,
            clear_group_wide=True,
            limit=limit
        )
        
        count_description = "всі повідомлення" if limit is None else f"останні {limit} повідомлень"
        await callback.answer(f"✅ Видалено {deleted_count} повідомлень", show_alert=True)
        keyboard = get_group_settings_keyboard(group, show_user_settings_button=is_admin)
        await refresh_group_menu(callback.message, group, keyboard)

    except Exception as e:
        logger.error(f"Error clearing group messages in chat {chat.id}: {e}", exc_info=True)
        await callback.answer("❌ Помилка при очищенні історії", show_alert=True)

@router.callback_query(F.data == "back_to_group_menu")
async def back_to_group_menu_callback(callback: CallbackQuery, group_dao: GroupDAO):
    """Return to the group menu."""
    chat = callback.message.chat
    is_admin = await is_user_group_admin(chat, callback.from_user.id)
    group = await get_group_or_none(group_dao, chat)
    if not group:
        await callback.answer("Групу не знайдено у базі", show_alert=True)
        return

    keyboard = get_group_settings_keyboard(group, show_user_settings_button=is_admin)
    await refresh_group_menu(callback.message, group, keyboard)