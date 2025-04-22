import logging
from aiogram import Router, filters
from aiogram.types import Message
from aiogram.enums import ChatType

from database.models import User
from database.dao import GroupDAO
from ..utils import is_user_group_admin, send_error_message, log_and_reply, get_group_or_none

logger = logging.getLogger(__name__)
router = Router()

@router.message(filters.Command("togglegrouptext"))
async def toggle_group_text_handler(
    message: Message,
    group_dao: GroupDAO,
    user: User
) -> None:
    """(Admin Only) Toggles bot text responses ON/OFF for this group."""
    chat = message.chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        await message.reply("Ця команда працює тільки в групах.")
        return

    sender_id = user.telegram_id
    if not await is_user_group_admin(chat, sender_id):
        logger.warning(f"User {sender_id} (not admin) tried to use /togglegrouptext in chat {chat.id}")
        await message.reply("Ви повинні бути адміністратором групи, щоб змінювати ці налаштування.")
        return

    group = await get_group_or_none(group_dao, chat)
    if not group:
        await send_error_message(message, "Помилка: не вдалося знайти дані цієї групи в базі.")
        return

    new_value = not group.responds_to_text
    success = await group_dao.update_group_settings(group_id=group.id, responds_to_text=new_value)

    if success:
        group.responds_to_text = new_value
        log_message = f"Admin {sender_id} toggled group {chat.id} (DB ID: {group.id}) responds_to_text to {new_value}"
        status = "<b>увімкнено</b>" if new_value else "<b>вимкнено</b>"
        reply_text = f"✅ Відповіді бота на текстові повідомлення у цій групі тепер {status}."
        await log_and_reply(message, log_message, reply_text)
    else:
        logger.error(f"Failed to update responds_to_text for group {group.id} (chat {chat.id}) in DB.")
        await send_error_message(message, "Не вдалося зберегти налаштування групи.")


@router.message(filters.Command("togglegroupvoice"))
async def toggle_group_voice_handler(
    message: Message,
    group_dao: GroupDAO,
    user: User
) -> None:
    """(Admin Only) Toggles bot voice processing ON/OFF for this group."""
    chat = message.chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        await message.reply("Ця команда працює тільки в групах.")
        return

    sender_id = user.telegram_id
    if not await is_user_group_admin(chat, sender_id):
        logger.warning(f"User {sender_id} (not admin) tried to use /togglegroupvoice in chat {chat.id}")
        await message.reply("Ви повинні бути адміністратором групи, щоб змінювати ці налаштування.")
        return

    group = await get_group_or_none(group_dao, chat)
    if not group:
        await send_error_message(message, "Помилка: не вдалося знайти дані цієї групи в базі.")
        return

    new_value = not group.responds_to_voice
    success = await group_dao.update_group_settings(group_id=group.id, responds_to_voice=new_value)

    if success:
        group.responds_to_voice = new_value
        log_message = f"Admin {sender_id} toggled group {chat.id} (DB ID: {group.id}) responds_to_voice to {new_value}"
        status = "<b>увімкнено</b>" if new_value else "<b>вимкнено</b>"
        reply_text = f"✅ Обробка ботом голосових повідомлень у цій групі тепер {status}."
        await log_and_reply(message, log_message, reply_text)
    else:
        logger.error(f"Failed to update responds_to_voice for group {group.id} (chat {chat.id}) in DB.")
        await send_error_message(message, "Не вдалося зберегти налаштування групи.")


@router.message(filters.Command("togglegroupphoto"))
async def toggle_group_photo_response_handler(
    message: Message,
    group_dao: GroupDAO,
    user: User
) -> None:
    """Toggles group photo response setting."""
    if not await is_user_group_admin(message):
        await message.answer("❌ Ця команда доступна тільки адміністраторам групи.")
        return

    chat = message.chat
    group = await group_dao.get_group_by_telegram_id(chat.id)
    if not group:
        await message.answer("❌ Група не знайдена в базі даних.")
        return

    new_value = not group.responds_to_photo
    success = await group_dao.update_group_settings(group_id=group.id, responds_to_photo=new_value)

    if success:
        group.responds_to_photo = new_value
        log_message = f"Group {group.telegram_chat_id} toggled responds_to_photo to {group.responds_to_photo}"
        status = "<b>увімкнено</b>" if group.responds_to_photo else "<b>вимкнено</b>"
        reply_text = f"✅ Відповіді на фото та зображення в цій групі тепер {status}."
        await log_and_reply(message, log_message, reply_text)
    else:
        logger.error(f"Failed to update responds_to_photo for group {group.telegram_chat_id} in DB.")
        await send_error_message(message, "Не вдалося зберегти налаштування групи.")