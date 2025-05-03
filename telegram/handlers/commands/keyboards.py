from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database.models import User

def get_settings_keyboard(user: User, show_group_settings_button=False) -> InlineKeyboardMarkup:
    """Creates an inline keyboard with user settings."""
    keyboard = [
        [
            InlineKeyboardButton(
                text=f"{'🟢' if not user.is_global_disabled else '🔴'} Глобальні відповіді {'(Увімк)' if not user.is_global_disabled else '(Вимк)'}",
                callback_data="toggle_global_disabled"
            )
        ]
    ]

    # Показывать кнопки настроек только если глобальные ответы включены
    if not user.is_global_disabled:
        keyboard.extend([
            [
                InlineKeyboardButton(
                    text=f"{'✅' if user.responds_to_text else '❌'} Текстові повідомлення {'(Увімк)' if user.responds_to_text else '(Вимк)'}",
                    callback_data="toggle_responds_to_text"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{'✅' if user.responds_to_voice else '❌'} Голосові повідомлення {'(Увімк)' if user.responds_to_voice else '(Вимк)'}",
                    callback_data="toggle_responds_to_voice"
                )
            ]
        ])

        # Показывать кнопку режима голосовых только если голосовые включены
        if user.responds_to_voice:
            keyboard.append([
                InlineKeyboardButton(
                    text=f"{'🎤' if not user.transcribe_voice_only else '📝'} Режим голосових: {'Відповідь' if not user.transcribe_voice_only else 'Транскрипція'}",
                    callback_data="toggle_transcribe_voice_only"
                )
            ])

        keyboard.extend([
            [
                InlineKeyboardButton(
                    text=f"{'✅' if user.responds_to_photo else '❌'} Фотографії {'(Увімк)' if user.responds_to_photo else '(Вимк)'}",
                    callback_data="toggle_responds_to_photo"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{'✅' if user.responds_to_video_note else '❌'} Відео-кружки {'(Увімк)' if user.responds_to_video_note else '(Вимк)'}",
                    callback_data="toggle_responds_to_video_note"
                )
            ]
        ])

        # Показывать кнопку режима видео-кружков только если они включены
        if user.responds_to_video_note:
            keyboard.append([
                InlineKeyboardButton(
                    text=f"{'🎥' if not user.transcribe_video_note else '📝'} Режим відео: {'Відповідь' if not user.transcribe_video_note else 'Транскрипція'}",
                    callback_data="toggle_transcribe_video_note"
                )
            ])

    # Остальные кнопки показываем всегда
    keyboard.extend([
        [
            InlineKeyboardButton(
                text="🗑 Очистити історію",
                callback_data="clear_messages"
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
    ])
    
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
                text=f"{'🟢' if not group.is_global_disabled else '🔴'} Глобальні відповіді {'(Увімк)' if not group.is_global_disabled else '(Вимк)'}",
                callback_data="toggle_group_global_disabled"
            )
        ]
    ]

    # Показывать кнопки настроек только если глобальные ответы включены
    if not group.is_global_disabled:
        keyboard.extend([
            [
                InlineKeyboardButton(
                    text=f"{'✅' if group.responds_to_text else '❌'} Текстові повідомлення {'(Увімк)' if group.responds_to_text else '(Вимк)'}",
                    callback_data="toggle_group_responds_to_text"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{'✅' if group.responds_to_voice else '❌'} Голосові повідомлення {'(Увімк)' if group.responds_to_voice else '(Вимк)'}",
                    callback_data="toggle_group_responds_to_voice"
                )
            ]
        ])

        # Показывать кнопку режима голосовых только если голосовые включены
        if group.responds_to_voice:
            keyboard.append([
                InlineKeyboardButton(
                    text=f"{'🎤' if not group.transcribe_voice_only else '📝'} Режим голосових: {'Відповідь' if not group.transcribe_voice_only else 'Транскрипція'}",
                    callback_data="toggle_group_transcribe_voice_only"
                )
            ])

        keyboard.extend([
            [
                InlineKeyboardButton(
                    text=f"{'✅' if group.responds_to_photo else '❌'} Фотографії {'(Увімк)' if group.responds_to_photo else '(Вимк)'}",
                    callback_data="toggle_group_responds_to_photo"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{'✅' if group.responds_to_video_note else '❌'} Відео-кружки {'(Увімк)' if group.responds_to_video_note else '(Вимк)'}",
                    callback_data="toggle_group_responds_to_video_note"
                )
            ]
        ])

        # Показывать кнопку режима видео-кружков только если они включены
        if group.responds_to_video_note:
            keyboard.append([
                InlineKeyboardButton(
                    text=f"{'🎥' if not group.transcribe_video_note else '📝'} Режим відео: {'Відповідь' if not group.transcribe_video_note else 'Транскрипція'}",
                    callback_data="toggle_group_transcribe_video_note"
                )
            ])

    # Всегда показываем кнопки управления
    keyboard.extend([
        [
            InlineKeyboardButton(
                text="ℹ️ Довідка",
                callback_data="show_group_help"
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
    ])
    
    # Кнопка перехода к пользовательским настройкам для админов и владельцев
    if show_user_settings_button:
        keyboard.append([
            InlineKeyboardButton(
                text="👤 Мої налаштування",
                callback_data="back_to_user_settings"
            )
        ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
