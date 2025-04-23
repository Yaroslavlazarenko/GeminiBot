import logging
from aiogram import Router, filters
from aiogram.types import Message

logger = logging.getLogger(__name__)
router = Router()

@router.message(filters.Command("help"))
async def help_handler(message: Message) -> None:
    """Shows detailed help about available commands."""
    help_text = (
        "📋 <b>Доступні команди:</b>\n\n"
        "• <code>/menu</code> - Показує інтерактивне меню для керування налаштуваннями бота\n\n"
        "• <code>/clear</code> - Команда для очищення історії повідомлень:\n"
        "  - <code>/clear</code> - Очищає <b>вашу</b> історію в поточному чаті\n"
        "  - <code>/clear &lt;число&gt;</code> - Очищає останні N <b>ваших</b> повідомлень\n"
        "  - <code>/clear group</code> - (Тільки для адміністраторів) Очищає <b>всю</b> історію групи\n"
        "  - <code>/clear group &lt;число&gt;</code> - (Тільки для адміністраторів) Очищає останні N повідомлень <b>групи</b>\n\n"
        "ℹ️ <i>Примітка: Всі інші налаштування доступні через інтерактивне меню (команда /menu)</i>"
    )
    await message.answer(help_text, parse_mode="HTML") 