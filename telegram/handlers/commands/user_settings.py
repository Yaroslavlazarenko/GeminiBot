import logging
from aiogram import Router, filters, F
from aiogram.types import Message
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest

from database.models import User
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
from ..utils import is_user_group_admin, send_error_message, log_and_reply

logger = logging.getLogger(__name__)
router = Router()

@router.message(filters.Command("settings"))
async def show_settings_handler(
    message: Message,
    user: User,
    group_dao: GroupDAO
) -> None:
    """
    Shows settings:
    - Global user settings
    - Group settings (if in a group)
    - Group management commands (only for admins)
    - History clearing commands
    """
    chat = message.chat

    user_text_status = f"✅ *Увімкнено*" if user.responds_to_text else f"❌ *Вимкнено*"
    user_voice_status = f"✅ *Увімкнено*" if user.responds_to_voice else f"❌ *Вимкнено*"
    user_photo_status = f"✅ *Увімкнено*" if user.responds_to_photo else f"❌ *Вимкнено*"
    user_video_note_status = f"✅ *Увімкнено*" if user.responds_to_video_note else f"❌ *Вимкнено*"
    if user.responds_to_voice:
        user_voice_mode = "*📝 Тільки транскрипція*" if user.transcribe_voice_only else "*💬 Відповідь на повідомлення*"
    else:
        user_voice_mode = "_(неактуально)_"

    user_settings_text = (
        f"👤 **Ваші глобальні налаштування:**\n"
        f"   - Відповіді на текст: {user_text_status}\n"
        f"   - Обробка голосу: {user_voice_status}\n"
        f"   - Обробка фото: {user_photo_status}\n"
        f"   - Обробка відео-повідомлень: {user_video_note_status}\n"
        f"   - Режим голосу: {user_voice_mode}\n\n"
        f"   *Змінити глобальні налаштування можна командами:* `{'/toggletext'}`, `{'/togglevoice'}`, `{'/togglemode'}`, `{'/togglephoto'}`, `{'/togglevideonote'}` (краще в приватному чаті).\n\n"
        f"   🧹 **Очищення історії:**\n"
        f"   `{'/clear'}` - Очистити *вашу* історію в цьому чаті\n"
        f"   `{'/clear <число>'}` - Очистити *ваші* останні N повідомлень"
    )

    public_group_settings_text = ""
    admin_group_settings_text = ""

    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        group = await group_dao.get_group_by_telegram_id(telegram_chat_id=chat.id)
        if group:
            group_text_status = f"✅ *Увімкнено*" if group.responds_to_text else f"❌ *Вимкнено*"
            group_voice_status = f"✅ *Увімкнено*" if group.responds_to_voice else f"❌ *Вимкнено*"
            public_group_settings_text = (
                f"\n\n🏢 **Налаштування для цієї групи ('{group.name}') (Загальні):**\n"
                f"   - Відповіді бота на текст: {group_text_status}\n"
                f"   - Обробка ботом голосу: {group_voice_status}"
            )

            is_admin = await is_user_group_admin(chat, user.telegram_id)
            if is_admin:
                admin_group_settings_text = (
                    f"\n\n🔑 **Налаштування для групи (Адміністраторам):**\n"
                    f"   *Ви можете змінити налаштування групи командами:*\n"
                    f"   `{'/togglegrouptext'}` - Увімк./Вимк. відповіді на текст\n"
                    f"   `{'/togglegroupvoice'}` - Увімк./Вимк. обробку голосу\n"
                    f"   `{'/togglegroupphoto'}` - Увімк./Вимк. обробку фото\n"
                    f"\n   *Очищення історії групи (тільки адміни):*\n"
                    f"   `{'/clear group'}` - Очистити *всю* історію групи\n"
                    f"   `{'/clear group <число>'}` - Очистити останні N повідомлень *групи*"
                )
        else:
            public_group_settings_text = "\n\n⚠️ Не вдалося отримати детальні налаштування для цієї групи з бази даних."

    full_settings_text = user_settings_text + public_group_settings_text + admin_group_settings_text
    try:
        await message.answer(full_settings_text, parse_mode="Markdown", disable_web_page_preview=True)
    except TelegramBadRequest as e:
        logger.error(f"Failed to send settings message (Markdown error?): {e}. Text: {full_settings_text[:500]}...") # Log part of text
        try:
            text_without_markdown = full_settings_text.replace('*', '').replace('_', '').replace('`', '')
            await message.answer(text_without_markdown, disable_web_page_preview=True)
        except Exception as fallback_e:
            logger.error(f"Failed to send settings message even without Markdown: {fallback_e}")
            await message.answer("Не вдалося відобразити налаштування.")

@router.message(filters.Command("toggletext"))
async def toggle_text_response_handler(
    message: Message,
    user_dao: UserDAO,
    user: User
) -> None:
    """Toggles user text response setting."""
    if message.chat.type != ChatType.PRIVATE:
         await message.answer("ℹ️ Зверніть увагу: ця команда змінює ваші глобальні налаштування для всіх чатів.")

    new_value = not user.responds_to_text
    success = await user_dao.update_user_settings(user_id=user.id, responds_to_text=new_value)

    if success:
        user.responds_to_text = new_value
        log_message = f"User {user.telegram_id} toggled responds_to_text to {user.responds_to_text}"
        status = "<b>увімкнено</b>" if user.responds_to_text else "<b>вимкнено</b>"
        reply_text = f"✅ Відповіді на текстові повідомлення тепер {status}."
        await log_and_reply(message, log_message, reply_text)
    else:
        logger.error(f"Failed to update responds_to_text for user {user.telegram_id} in DB.")
        await send_error_message(message, "Не вдалося зберегти налаштування.")

@router.message(filters.Command("togglevoice"))
async def toggle_voice_response_handler(
    message: Message,
    user_dao: UserDAO,
    user: User
) -> None:
    """Toggles user voice processing setting."""
    if message.chat.type != ChatType.PRIVATE:
         await message.answer("ℹ️ Зверніть увагу: ця команда змінює ваші глобальні налаштування для всіх чатів.")

    new_value = not user.responds_to_voice
    success = await user_dao.update_user_settings(user_id=user.id, responds_to_voice=new_value)

    if success:
        user.responds_to_voice = new_value
        log_message = f"User {user.telegram_id} toggled responds_to_voice to {user.responds_to_voice}"
        status = "<b>увімкнено</b>" if user.responds_to_voice else "<b>вимкнено</b>"
        reply_text = f"✅ Обробка голосових повідомлень тепер {status}."
        await log_and_reply(message, log_message, reply_text)
    else:
        logger.error(f"Failed to update responds_to_voice for user {user.telegram_id} in DB.")
        await send_error_message(message, "Не вдалося зберегти налаштування.")


@router.message(filters.Command("togglemode"))
async def toggle_voice_mode_handler(
    message: Message,
    user_dao: UserDAO,
    user: User
) -> None:
    """Toggles user voice processing mode."""
    if message.chat.type != ChatType.PRIVATE:
         await message.answer("ℹ️ Зверніть увагу: ця команда змінює ваші глобальні налаштування для всіх чатів.")

    if not user.responds_to_voice:
        await message.answer(f"Спочатку увімкніть обробку голосових повідомлень командою <code>{'/togglevoice'}</code>.", parse_mode="HTML")
        return

    new_value = not user.transcribe_voice_only
    success = await user_dao.update_user_settings(user_id=user.id, transcribe_voice_only=new_value)

    if success:
        user.transcribe_voice_only = new_value
        log_message = f"User {user.telegram_id} toggled transcribe_voice_only to {user.transcribe_voice_only}"
        mode = "<b>тільки транскрибувати</b>" if user.transcribe_voice_only else "<b>відповідати на повідомлення</b>"
        reply_text = f"✅ Режим обробки голосових змінено: бот буде {mode}."
        await log_and_reply(message, log_message, reply_text)
    else:
        logger.error(f"Failed to update transcribe_voice_only for user {user.telegram_id} in DB.")
        await send_error_message(message, "Не вдалося зберегти налаштування.")

@router.message(filters.Command("togglephoto"))
async def toggle_photo_response_handler(
    message: Message,
    user_dao: UserDAO,
    user: User
) -> None:
    """Toggles user photo response setting."""
    if message.chat.type != ChatType.PRIVATE:
         await message.answer("ℹ️ Зверніть увагу: ця команда змінює ваші глобальні налаштування для всіх чатів.")

    new_value = not user.responds_to_photo
    success = await user_dao.update_user_settings(user_id=user.id, responds_to_photo=new_value)

    if success:
        user.responds_to_photo = new_value
        log_message = f"User {user.telegram_id} toggled responds_to_photo to {user.responds_to_photo}"
        status = "<b>увімкнено</b>" if user.responds_to_photo else "<b>вимкнено</b>"
        reply_text = f"✅ Відповіді на фото та зображення тепер {status}."
        await log_and_reply(message, log_message, reply_text)
    else:
        logger.error(f"Failed to update responds_to_photo for user {user.telegram_id} in DB.")
        await send_error_message(message, "Не вдалося зберегти налаштування.")

@router.message(filters.Command("togglevideonote"))
async def toggle_video_note_response_handler(
    message: Message,
    user_dao: UserDAO,
    user: User
) -> None:
    """Toggles user video note response setting."""
    if message.chat.type != ChatType.PRIVATE:
         await message.answer("ℹ️ Зверніть увагу: ця команда змінює ваші глобальні налаштування для всіх чатів.")

    new_value = not user.responds_to_video_note
    success = await user_dao.update_user_settings(user_id=user.id, responds_to_video_note=new_value)

    if success:
        user.responds_to_video_note = new_value
        log_message = f"User {user.telegram_id} toggled responds_to_video_note to {user.responds_to_video_note}"
        status = "<b>увімкнено</b>" if user.responds_to_video_note else "<b>вимкнено</b>"
        reply_text = f"✅ Відповіді на відео-повідомлення тепер {status}."
        await log_and_reply(message, log_message, reply_text)
    else:
        logger.error(f"Failed to update responds_to_video_note for user {user.telegram_id} in DB.")
        await send_error_message(message, "Не вдалося зберегти налаштування.")