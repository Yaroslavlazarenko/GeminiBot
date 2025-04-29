from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database.models import User

def get_settings_keyboard(user: User, show_group_settings_button=False) -> InlineKeyboardMarkup:
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
                text="ℹ️ Довідка",
                callback_data="show_help"
            ),
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
    # Кнопка перехода к настройкам группы для админов
    if show_group_settings_button:
        keyboard.append([
            InlineKeyboardButton(
                text="⚙️ Налаштування групи",
                callback_data="open_group_settings_menu"
            )
        ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


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
                text="ℹ️ Довідка",
                callback_data="show_help"
            ),
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
