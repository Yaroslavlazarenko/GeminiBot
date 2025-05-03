import logging
from aiogram import Router, F, filters
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from aiogram.exceptions import TelegramBadRequest

from database.models import User
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
from ..utils import get_group_or_none, is_user_group_admin, rate_limited_edit


logger = logging.getLogger(__name__)
router = Router()

from .keyboards import get_settings_keyboard

@router.message(filters.Command("menu"))
async def show_menu(message: Message, user: User):
    """Handler for the /menu command."""
    if message.chat.type in ["group", "supergroup"]:
        is_admin = await is_user_group_admin(message.chat, user.telegram_id)
        keyboard = get_settings_keyboard(user, show_group_settings_button=is_admin)
        
        # Get active settings count
        active_settings = sum([
            not user.is_global_disabled,
            user.responds_to_text,
            user.responds_to_voice,
            user.responds_to_photo,
            user.responds_to_video_note
        ])
        
        await message.answer(
            f"🎛 <b>Особисті налаштування</b>\n"
            f"{'🟢' if not user.is_global_disabled else '🔴'} Загальний статус: {'увімкнено' if not user.is_global_disabled else 'вимкнено'}\n"
            f"📊 Активно налаштувань: {active_settings}/5\n\n"
            "Використовуйте кнопки нижче для керування:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return
        
    # Для личных чатов
    keyboard = get_settings_keyboard(user)
    
    # Get active settings count
    active_settings = sum([
        not user.is_global_disabled,
        user.responds_to_text,
        user.responds_to_voice,
        user.responds_to_photo,
        user.responds_to_video_note
    ])
    
    await message.answer(
        f"🎛 <b>Налаштування бота</b>\n"
        f"{'🟢' if not user.is_global_disabled else '🔴'} Загальний статус: {'увімкнено' if not user.is_global_disabled else 'вимкнено'}\n"
        f"📊 Активно налаштувань: {active_settings}/5\n\n"
        "Використовуйте кнопки нижче для керування:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(F.data == "back_to_user_settings")
async def back_to_user_settings_callback(callback: CallbackQuery, user: User):

    chat = callback.message.chat
    is_admin = await is_user_group_admin(chat, user.telegram_id)
    keyboard = get_settings_keyboard(user, show_group_settings_button=is_admin)
    await rate_limited_edit(
        callback.message,
        text="🎛 <b>Головне меню</b>\n\nКеруйте налаштуваннями бота за допомогою кнопок нижче:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "open_group_settings_menu")
async def open_group_settings_menu_callback(callback: CallbackQuery, group_dao: GroupDAO):
    from ..utils import get_group_or_none, is_user_group_admin
    chat = callback.message.chat
    is_admin = await is_user_group_admin(chat, callback.from_user.id)
    group = await get_group_or_none(group_dao, chat)
    if not group:
        await callback.answer("Групу не знайдено у базі", show_alert=True)
        return
    from .group_inline_menu import get_group_settings_keyboard
    keyboard = get_group_settings_keyboard(group, show_user_settings_button=is_admin)
    await rate_limited_edit(
        callback.message,
        text="👥 <b>Налаштування групи</b>\n\nКеруйте груповими параметрами бота:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "toggle_global_disabled")
async def toggle_global_callback(callback: CallbackQuery, user: User, user_dao: UserDAO):
    """Handle global response toggle."""
    new_value = not user.is_global_disabled
    success = await user_dao.update_user_settings(user_id=user.id, is_global_disabled=new_value)
    
    if success:
        user.is_global_disabled = new_value
        status = "увімкнено" if not new_value else "вимкнено"
        await callback.answer(f"✅ Глобальні відповіді {status}")
                
        chat = callback.message.chat
        is_admin = False
        if chat.type in ["group", "supergroup"]:
            is_admin = await is_user_group_admin(chat, user.telegram_id)
        await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user, show_group_settings_button=is_admin))
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(F.data == "toggle_responds_to_text")
async def toggle_text_callback(callback: CallbackQuery, user: User, user_dao: UserDAO):
    """Handle text response toggle."""
    new_value = not user.responds_to_text
    success = await user_dao.update_user_settings(user_id=user.id, responds_to_text=new_value)
    
    if success:
        user.responds_to_text = new_value
        status = "увімкнено" if new_value else "вимкнено"
        await callback.answer(f"✅ Відповіді на текст {status}")
        chat = callback.message.chat
        is_admin = False
        if chat.type in ["group", "supergroup"]:
            is_admin = await is_user_group_admin(chat, user.telegram_id)
        await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user, show_group_settings_button=is_admin))
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(F.data == "toggle_responds_to_voice")
async def toggle_voice_callback(callback: CallbackQuery, user: User, user_dao: UserDAO):
    """Handle voice processing toggle."""
    new_value = not user.responds_to_voice
    success = await user_dao.update_user_settings(user_id=user.id, responds_to_voice=new_value)
    
    if success:
        user.responds_to_voice = new_value
        status = "увімкнено" if new_value else "вимкнено"
        await callback.answer(f"✅ Обробку голосу {status}")
        chat = callback.message.chat
        is_admin = False
        if chat.type in ["group", "supergroup"]:
            is_admin = await is_user_group_admin(chat, user.telegram_id)
        await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user, show_group_settings_button=is_admin))
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(F.data == "toggle_responds_to_photo")
async def toggle_photo_callback(callback: CallbackQuery, user: User, user_dao: UserDAO):
    """Handle photo processing toggle."""
    new_value = not user.responds_to_photo
    success = await user_dao.update_user_settings(user_id=user.id, responds_to_photo=new_value)
    
    if success:
        user.responds_to_photo = new_value
        status = "увімкнено" if new_value else "вимкнено"
        await callback.answer(f"✅ Обробку фото {status}")
        chat = callback.message.chat
        is_admin = False
        if chat.type in ["group", "supergroup"]:
            is_admin = await is_user_group_admin(chat, user.telegram_id)
        await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user, show_group_settings_button=is_admin))
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(F.data == "toggle_responds_to_video_note")
async def toggle_video_note_callback(callback: CallbackQuery, user: User, user_dao: UserDAO):
    """Handle video note processing toggle."""
    new_value = not user.responds_to_video_note
    success = await user_dao.update_user_settings(user_id=user.id, responds_to_video_note=new_value)
    
    if success:
        user.responds_to_video_note = new_value
        status = "увімкнено" if new_value else "вимкнено"
        await callback.answer(f"✅ Обробку відео-повідомлень {status}")
        chat = callback.message.chat
        is_admin = False
        if chat.type in ["group", "supergroup"]:
            is_admin = await is_user_group_admin(chat, user.telegram_id)
        await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user, show_group_settings_button=is_admin))
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(F.data == "toggle_transcribe_voice_only")
async def toggle_mode_callback(callback: CallbackQuery, user: User, user_dao: UserDAO):
    """Handle voice mode toggle."""
    if not user.responds_to_voice:
        await callback.answer("Спочатку увімкніть обробку голосу", show_alert=True)
        return
        
    new_value = not user.transcribe_voice_only
    success = await user_dao.update_user_settings(user_id=user.id, transcribe_voice_only=new_value)
    
    if success:
        user.transcribe_voice_only = new_value
        mode = "транскрипція" if new_value else "відповідь"
        await callback.answer(f"✅ Режим голосу: {mode}")
        chat = callback.message.chat
        is_admin = False
        if chat.type in ["group", "supergroup"]:
            is_admin = await is_user_group_admin(chat, user.telegram_id)
        await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user, show_group_settings_button=is_admin))
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(F.data == "toggle_transcribe_video_note")
async def toggle_transcribe_video_note_callback(callback: CallbackQuery, user: User, user_dao: UserDAO):
    """Handle video note transcription mode toggle."""
    if not user.responds_to_video_note:
        await callback.answer("Спочатку увімкніть обробку відео-повідомлень", show_alert=True)
        return
        
    new_value = not user.transcribe_video_note
    success = await user_dao.update_user_settings(user_id=user.id, transcribe_video_note=new_value)
    
    if success:
        user.transcribe_video_note = new_value
        mode = "транскрипція" if new_value else "відповідь"
        await callback.answer(f"✅ Режим відео: {mode}")
        chat = callback.message.chat
        is_admin = False
        if chat.type in ["group", "supergroup"]:
            is_admin = await is_user_group_admin(chat, user.telegram_id)
        await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user, show_group_settings_button=is_admin))
    else:
        await callback.answer("❌ Помилка при зміні налаштувань", show_alert=True)

@router.callback_query(F.data == "clear_messages")
async def clear_messages_callback(callback: CallbackQuery, user: User, message_dao: MessageHistoryDAO, group_dao: GroupDAO):
    """Handle message history clearing."""
    chat = callback.message.chat
    group = await get_group_or_none(group_dao, chat)
    group_db_id = group.id if group else None

    try:
        deleted_count = await message_dao.clear_history(
            user_id=user.id,
            group_id=group_db_id,
            clear_group_wide=False
        )

        target_description = "особисту історію" if not group else f"вашу історію у групі '{group.name}'"
        await callback.answer(f"✅ Видалено {deleted_count} повідомлень", show_alert=True)
        
        # Only update the keyboard if messages were actually deleted
        if deleted_count > 0:
            chat = callback.message.chat
            is_admin = False
            if chat.type in ["group", "supergroup"]:
                is_admin = await is_user_group_admin(chat, user.telegram_id)
            await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user, show_group_settings_button=is_admin))

    except Exception as e:
        logger.error(f"Error clearing messages for user {user.telegram_id} in chat {chat.id}: {e}", exc_info=True)
        await callback.answer("❌ Помилка при очищенні історії", show_alert=True)

@router.callback_query(F.data == "close_user_help")
async def close_user_help_callback(callback: CallbackQuery, user: User):
    chat = callback.message.chat
    is_admin = False
    if chat.type in ["group", "supergroup"]:
        is_admin = await is_user_group_admin(chat, user.telegram_id)
    keyboard = get_settings_keyboard(user, show_group_settings_button=is_admin)
    await rate_limited_edit(
        callback.message,
        text="🎛 <b>Головне меню</b>\n\nКеруйте налаштуваннями бота за допомогою кнопок нижче:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(F.data == "show_help")
async def show_help_callback(callback: CallbackQuery):
    """Show help message."""
    help_text = (
        "📋 <b>Довідка по налаштуванням:</b>\n\n"
        "• <b>Глобальні відповіді</b> — Увімкнення/вимкнення всіх відповідей бота.\n\n"
        "• <b>Відповіді на текст</b> — Бот буде відповідати на ваші текстові повідомлення.\n\n"
        "• <b>Відповіді на голосові</b> — Бот буде відповідати на ваші голосові повідомлення.\n\n"
        "• <b>Відповіді на фото</b> — Бот буде відповідати на фото.\n\n"
        "• <b>Відповіді на відео-кружки</b> — Бот буде відповідати на відео-кружки.\n\n"
        "• <b>Транскрипція голосових</b> — Бот буде перетворювати голосові у текст.\n\n"
        "• <b>Транскрипція відео-кружків</b> — Бот буде перетворювати відео-кружки у текст.\n\n"
        "\nНатисніть на кнопку, щоб змінити відповідне налаштування."
    )
    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Закрити довідку", callback_data="close_user_help")
    ]])
    await rate_limited_edit(
        callback.message,
        text=help_text,
        parse_mode="HTML",
        reply_markup=keyboard
    )

@router.callback_query(F.data == "close_menu")
async def close_menu_callback(callback: CallbackQuery):
    """Handler for closing the menu."""
    await callback.message.delete()
    await callback.answer("Меню закрито")

@router.callback_query(F.data == "refresh_menu")
async def refresh_menu_callback(callback: CallbackQuery, user: User):
    """Handler for refreshing the menu."""
    try:
        chat = callback.message.chat
        is_admin = False
        if chat.type in ["group", "supergroup"]:
            is_admin = await is_user_group_admin(chat, user.telegram_id)
        keyboard = get_settings_keyboard(user, show_group_settings_button=is_admin)
        await callback.message.edit_reply_markup(reply_markup=keyboard)
        await callback.answer("Меню оновлено")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer("Меню вже актуальне")
        else:
            logger.error(f"Error refreshing menu: {e}")
            await callback.answer("❌ Помилка при оновленні меню", show_alert=True)