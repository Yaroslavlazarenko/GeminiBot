import logging
from aiogram import Router, F, filters
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

from database.models import User
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
from ..utils import get_group_or_none

logger = logging.getLogger(__name__)
router = Router()

def get_settings_keyboard(user: User) -> InlineKeyboardMarkup:
    """Creates an inline keyboard with user settings."""
    keyboard = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if not user.is_global_disabled else '❌'} Відповідати на повідомлення",
                callback_data="toggle_global_disabled"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{'✅' if user.responds_to_text else '❌'} Відповідати на текст",
                callback_data="toggle_responds_to_text"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{'✅' if user.responds_to_voice else '❌'} Відповідати на голосові",
                callback_data="toggle_responds_to_voice"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{'✅' if user.responds_to_photo else '❌'} Відповідати на фото",
                callback_data="toggle_responds_to_photo"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{'✅' if user.responds_to_video_note else '❌'} Відповідати на відео-кружки",
                callback_data="toggle_responds_to_video_note"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{'✅' if user.transcribe_voice_only else '❌'} Транскрибувати голосові",
                callback_data="toggle_transcribe_voice_only"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{'✅' if user.transcribe_video_note else '❌'} Транскрибувати відео-кружки",
                callback_data="toggle_transcribe_video_note"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔄 Оновити",
                callback_data="refresh_menu"
            ),
            InlineKeyboardButton(
                text="❌ Закрити",
                callback_data="close_menu"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@router.message(filters.Command("menu"))
async def show_menu(message: Message, user: User):
    """Handler for the /menu command."""
    keyboard = get_settings_keyboard(user)
    await message.answer(
        "🎛 <b>Головне меню</b>\n\n"
        "Керуйте налаштуваннями бота за допомогою кнопок нижче:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(F.data == "toggle_global_disabled")
async def toggle_global_callback(callback: CallbackQuery, user: User, user_dao: UserDAO):
    """Handle global response toggle."""
    new_value = not user.is_global_disabled
    success = await user_dao.update_user_settings(user_id=user.id, is_global_disabled=new_value)
    
    if success:
        user.is_global_disabled = new_value
        status = "увімкнено" if not new_value else "вимкнено"
        await callback.answer(f"✅ Глобальні відповіді {status}")
        await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user))
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
        await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user))
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
        await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user))
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
        await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user))
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
        await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user))
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
        await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user))
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
        await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user))
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
            await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user))

    except Exception as e:
        logger.error(f"Error clearing messages for user {user.telegram_id} in chat {chat.id}: {e}", exc_info=True)
        await callback.answer("❌ Помилка при очищенні історії", show_alert=True)

@router.callback_query(F.data == "show_help")
async def show_help_callback(callback: CallbackQuery, user: User):
    """Show help message."""
    help_text = (
        "📋 <b>Довідка по налаштуванням:</b>\n\n"
        "• <b>Глобальні відповіді</b> - Увімкнення/вимкнення всіх відповідей бота\n\n"
        "• <b>Відповіді на текст</b> - Бот буде відповідати на ваші текстові повідомлення\n\n"
        "• <b>Обробка голосу</b> - Бот буде обробляти ваші голосові повідомлення\n\n"
        "• <b>Обробка фото</b> - Бот буде аналізувати та відповідати на фото\n\n"
        "• <b>Обробка відео</b> - Бот буде аналізувати та відповідати на відео-повідомлення\n\n"
        "• <b>Режим голосу</b>:\n"
        "  - 📝 <i>Тільки транскрипція</i> - Бот лише перетворить голос у текст\n"
        "  - 💬 <i>Відповідь на голос</i> - Бот відповість на зміст голосового\n\n"
        "• <b>Очистити історію</b> - Видалити всю історію спілкування з ботом у поточному чаті\n\n"
        "Натисніть на кнопку, щоб змінити відповідне налаштування"
    )
    await callback.answer()
    keyboard = get_settings_keyboard(user)
    await callback.message.edit_text(
        help_text,
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
        keyboard = get_settings_keyboard(user)
        await callback.message.edit_reply_markup(reply_markup=keyboard)
        await callback.answer("Меню оновлено")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer("Меню вже актуальне")
        else:
            logger.error(f"Error refreshing menu: {e}")
            await callback.answer("❌ Помилка при оновленні меню", show_alert=True)