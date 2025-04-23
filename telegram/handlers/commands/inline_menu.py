import logging
from aiogram import Router, F, filters
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import User
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
from ..utils import get_group_or_none

logger = logging.getLogger(__name__)
router = Router()

def get_settings_keyboard(user: User) -> InlineKeyboardMarkup:
    """Create an inline keyboard for user settings."""
    builder = InlineKeyboardBuilder()
    
    # Text responses toggle button
    text_status = "✅" if user.responds_to_text else "❌"
    builder.button(text=f"Відповіді на текст {text_status}", callback_data="toggle_text")
    
    # Voice processing toggle button
    voice_status = "✅" if user.responds_to_voice else "❌"
    builder.button(text=f"Обробка голосу {voice_status}", callback_data="toggle_voice")
    
    # Voice mode button (only shown if voice processing is enabled)
    if user.responds_to_voice:
        mode_text = "📝 Тільки транскрипція" if user.transcribe_voice_only else "💬 Відповідь на голос"
        builder.button(text=mode_text, callback_data="toggle_mode")
    
    # Clear messages button
    builder.button(text="🗑 Очистити історію", callback_data="clear_messages")
    
    # Help button
    builder.button(text="❓ Допомога", callback_data="show_help")
    
    # Adjust the layout: 2 buttons per row
    builder.adjust(2)
    return builder.as_markup()

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

@router.callback_query(F.data == "toggle_text")
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

@router.callback_query(F.data == "toggle_voice")
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

@router.callback_query(F.data == "toggle_mode")
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
        "• <b>Відповіді на текст</b> - Бот буде відповідати на ваші текстові повідомлення\n\n"
        "• <b>Обробка голосу</b> - Бот буде обробляти ваші голосові повідомлення\n\n"
        "• <b>Режим голосу</b>:\n"
        "  - 📝 <i>Тільки транскрипція</i> - Бот лише перетворить голос у текст\n"
        "  - 💬 <i>Відповідь на голос</i> - Бот відповість на зміст голосового\n\n"
        "• <b>Очистити історію</b> - Видалити всю історію спілкування з ботом у поточному чаті\n\n"
        "Натисніть на кнопку, щоб змінити відповідне налаштування"
    )
    await callback.answer()
    # Use the user object from middleware instead of callback.from_user
    keyboard = get_settings_keyboard(user)
    await callback.message.edit_text(
        help_text,
        parse_mode="HTML",
        reply_markup=keyboard
    )