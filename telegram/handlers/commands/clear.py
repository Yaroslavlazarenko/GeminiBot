import logging
from aiogram import Router, filters
from aiogram.types import Message
from aiogram.enums import ChatType

from database.models import User
from database.dao import GroupDAO, MessageHistoryDAO
from ..utils import is_user_group_admin, send_error_message

logger = logging.getLogger(__name__)
router = Router()

@router.message(filters.Command("clear"))
async def clear_history_handler(
    message: Message,
    command: filters.CommandObject,
    group_dao: GroupDAO,          
    message_dao: MessageHistoryDAO,
    user: User
) -> None:
    """
    Clears message history based on arguments:
    /clear - Clears YOUR history in current chat (private or group).
    /clear <number> - Clears last <number> of YOUR messages in current chat.
    /clear group - (Groups only, admins only) Clears ALL group history.
    /clear group <number> - (Groups only, admins only) Clears last <number> messages of ALL group history.
    """
    chat_type = message.chat.type
    args = command.args.split() if command.args else []
    chat_id_for_log = message.chat.id
    user_id_for_log = user.telegram_id

    limit: int | None = None
    target_group_wide: bool = False
    target_description: str = ""
    group_db_id: int | None = None

    try:
        if chat_type == ChatType.PRIVATE:
            if len(args) == 0:
                target_description = "вашу особисту історію повідомлень"
            elif len(args) == 1:
                try:
                    limit = int(args[0])
                    if limit <= 0: raise ValueError("Limit must be positive.")
                    target_description = f"останні {limit} ваших особистих повідомлень"
                except ValueError:
                    await send_error_message(message, "Невірний формат. Очікується </code>/clear</code> або </code>/clear &lt;число&gt;</code> у приватних повідомленнях.")
                    return
            else:
                await send_error_message(message, "Невірний формат. Забагато аргументів для приватного чату.")
                return

        elif chat_type in [ChatType.GROUP, ChatType.SUPERGROUP]:
            group = await group_dao.get_group_by_telegram_id(telegram_chat_id=message.chat.id)
            if not group:
                logger.warning(f"User {user_id_for_log} tried /clear in group {chat_id_for_log}, but group not found in DB.")
                await message.answer("⚠️ Не вдалося знайти цей чат у базі даних. Будь ласка, спробуйте відправити звичайне повідомлення боту в цій групі, щоб він її зареєстрував.")
                return
            group_db_id = group.id

            if len(args) == 0:
                target_description = f"ваші повідомлення у групі '{group.name}'"
            elif len(args) == 1:
                if args[0].lower() == "group":
                    target_group_wide = True
                    target_description = f"всі повідомлення у групі '{group.name}'"
                else:
                    try:
                        limit = int(args[0])
                        if limit <= 0: raise ValueError("Limit must be positive.")
                        target_description = f"останні {limit} ваших повідомлень у групі '{group.name}'"
                    except ValueError:
                        await send_error_message(message, "Невірний формат. Очікується <code>/clear</code>, <code>/clear &lt;число&gt;</code>, <code>/clear group</code> або <code>/clear group &lt;число&gt;</code>.")
                        return
            elif len(args) == 2:
                if args[0].lower() == "group":
                    target_group_wide = True
                    try:
                        limit = int(args[1])
                        if limit <= 0: raise ValueError("Limit must be positive.")
                        target_description = f"останні {limit} повідомлень у групі '{group.name}'"
                    except ValueError:
                        await send_error_message(message, "Невірний формат. Очікується <code>/clear group &lt;число&gt;</code> (число повинно бути позитивним).")
                        return
                else:
                    await send_error_message(message, "Невірний формат. Очікується <code>/clear group &lt;число&gt;</code>.")
                    return
            else:
                await send_error_message(message, "Невірний формат. Забагато аргументів.")
                return

            if target_group_wide:
                is_admin = await is_user_group_admin(message.chat, user.telegram_id)
                if not is_admin:
                    logger.warning(f"User {user_id_for_log} (not admin) tried group-wide clear in group {chat_id_for_log}")
                    await message.answer("❌ Цю дію (очищення історії всієї групи) можуть виконувати лише адміністратори групи.")
                    return

        else:
            logger.warning(f"Unsupported chat type '{chat_type}' for /clear command from user {user_id_for_log}.")
            await message.answer("Ця команда підтримується лише в особистих чатах та групах.")
            return

        logger.info(f"User {user_id_for_log} requested history clear: chat_type={chat_type}, args={args}, target_group_wide={target_group_wide}, limit={limit}, group_db_id={group_db_id}")

        deleted_count = await message_dao.clear_history(
            user_id=user.id if not target_group_wide else None,
            group_id=group_db_id,
            clear_group_wide=target_group_wide,
            limit=limit
        )

        await message.answer(f"🗑 Історію очищено (видалено {deleted_count} повідомлень: {target_description}).")

    except ValueError as ve:
        logger.warning(f"ValueError during /clear for user {user_id_for_log} in chat {chat_id_for_log}: {ve}")
        await send_error_message(message, f"Помилка обробки команди: {ve}")
    except Exception as e:
        logger.error(f"Handler error during history clear for user {user_id_for_log} in chat {chat_id_for_log}: {e}", exc_info=True)
        await send_error_message(message, "Сталася неочікувана помилка під час очищення історії.")