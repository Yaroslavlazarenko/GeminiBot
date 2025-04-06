# telegram/handlers/response_handlers.py

import logging
import asyncio
from typing import cast, Dict, Any, List # Добавлены Dict, Any

from aiogram import F, Router, filters
from aiogram.types import Message, Chat, ChatMemberAdministrator, ChatMemberOwner
from aiogram.enums import ChatType, ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError

# Обновляем импорты из gemini.get_responses
from gemini.get_responses import get_text_response, get_audio_response

from services.database.models import User, Group, MessageRole
from services.database.dao import AsyncDAO

logger = logging.getLogger(__name__)
router = Router()


async def is_user_group_admin(chat: Chat, user_id: int) -> bool:
    """Checks if a user is an administrator or owner in a group/supergroup."""
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return False # Not applicable in private chats
    try:
        member = await chat.get_member(user_id)
        # return member.status in ['administrator', 'creator'] # Old way
        return member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]
    except TelegramBadRequest as e:
        # Common causes: User not in chat, bot doesn't have rights to get member list
        logger.warning(f"Could not get member status for user {user_id} in chat {chat.id}: {e}")
        return False
    except TelegramForbiddenError:
        logger.warning(f"Bot is forbidden from getting member status in chat {chat.id}. Cannot verify admin.")
        return False # Can't verify, assume not admin
    except Exception as e:
        logger.error(f"Unexpected error checking admin status for user {user_id} in chat {chat.id}: {e}", exc_info=True)
        return False

# --- Utility functions (без изменений) ---
async def send_error_message(message: Message, error_text: str) -> None:
    """Sends an error message to the user/chat."""
    try:
        await message.answer(error_text, parse_mode="Markdown")
    except TelegramBadRequest as e:
        logger.warning(f"Failed to send error message to chat {message.chat.id}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error sending error message to chat {message.chat.id}: {e}", exc_info=True)

async def log_and_reply(message: Message, log_message: str, reply_text: str, level: int = logging.INFO) -> None:
    """Logs a message and sends a reply to the user/chat."""
    logger.log(level, log_message)
    try:
        await message.answer(reply_text, parse_mode="Markdown")
    except TelegramBadRequest as e:
        logger.warning(f"Failed to send reply message to chat {message.chat.id}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error sending reply message to chat {message.chat.id}: {e}", exc_info=True)


@router.message(filters.Command("togglegrouptext"))
async def toggle_group_text_handler(message: Message, dao: AsyncDAO, user: User) -> None:
    """(Admin Only) Toggles bot text responses ON/OFF for this group."""
    chat = message.chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        await message.reply("Ця команда працює тільки в групах.")
        return

    # Permission Check
    sender_id = user.telegram_id # Or message.from_user.id if user object might not be fully synced
    if not await is_user_group_admin(chat, sender_id):
        logger.warning(f"User {sender_id} (not admin) tried to use /togglegrouptext in chat {chat.id}")
        await message.reply("Ви повинні бути адміністратором групи, щоб змінювати ці налаштування.")
        return

    group = await get_group_or_none(dao, chat)
    if not group:
        await send_error_message(message, "Помилка: не вдалося знайти дані цієї групи в базі.")
        return

    new_value = not group.responds_to_text
    success = await dao.update_group_settings(group_id=group.id, responds_to_text=new_value)

    if success:
        group.responds_to_text = new_value # Update in-memory object
        log_message = f"Admin {sender_id} toggled group {chat.id} (DB ID: {group.id}) responds_to_text to {new_value}"
        status = "*увімкнено*" if new_value else "*вимкнено*"
        reply_text = f"✅ Відповіді бота на текстові повідомлення у цій групі тепер {status}."
        await log_and_reply(message, log_message, reply_text)
    else:
        logger.error(f"Failed to update responds_to_text for group {group.id} (chat {chat.id}) in DB.")
        await send_error_message(message, "Не вдалося зберегти налаштуванн  я групи.")


@router.message(filters.Command("togglegroupvoice"))
async def toggle_group_voice_handler(message: Message, dao: AsyncDAO, user: User) -> None:
    """(Admin Only) Toggles bot voice processing ON/OFF for this group."""
    chat = message.chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        await message.reply("Ця команда працює тільки в групах.")
        return

    # Permission Check
    sender_id = user.telegram_id
    if not await is_user_group_admin(chat, sender_id):
        logger.warning(f"User {sender_id} (not admin) tried to use /togglegroupvoice in chat {chat.id}")
        await message.reply("Ви повинні бути адміністратором групи, щоб змінювати ці налаштування.")
        return

    group = await get_group_or_none(dao, chat)
    if not group:
        await send_error_message(message, "Помилка: не вдалося знайти дані цієї групи в базі.")
        return

    new_value = not group.responds_to_voice
    success = await dao.update_group_settings(group_id=group.id, responds_to_voice=new_value)

    if success:
        group.responds_to_voice = new_value # Update in-memory object
        log_message = f"Admin {sender_id} toggled group {chat.id} (DB ID: {group.id}) responds_to_voice to {new_value}"
        status = "*увімкнено*" if new_value else "*вимкнено*"
        reply_text = f"✅ Обробка ботом голосових повідомлень у цій групі тепер {status}."
        await log_and_reply(message, log_message, reply_text)
    else:
        logger.error(f"Failed to update responds_to_voice for group {group.id} (chat {chat.id}) in DB.")
        await send_error_message(message, "Не вдалося зберегти налаштування групи.")



# --- Command handlers (без изменений, т.к. они не вызывают Gemini напрямую) ---
@router.message(filters.Command("settings"))
async def show_settings_handler(message: Message, user: User, dao: AsyncDAO) -> None:
    """
    Показывает настройки:
    - Глобальные для пользователя.
    - Общие для группы (если в группе).
    - Команды управления группой (только для админов группы).
    - Команду очистки истории.
    """
    chat = message.chat

    # --- 1. User Global Settings (Always shown) ---
    user_text_status = f"✅ *Увімкнено*" if user.responds_to_text else f"❌ *Вимкнено*"
    user_voice_status = f"✅ *Увімкнено*" if user.responds_to_voice else f"❌ *Вимкнено*"
    if user.responds_to_voice:
        user_voice_mode = "*📝 Тільки транскрипція*" if user.transcribe_voice_only else "*💬 Відповідь на повідомлення*"
    else:
        user_voice_mode = "_(неактуально)_"

    user_settings_text = (
        f"👤 **Ваші глобальні налаштування:**\n"
        f"   - Відповіді на текст: {user_text_status}\n"
        f"   - Обробка голосу: {user_voice_status}\n"
        f"   - Режим голосу: {user_voice_mode}\n\n"
        f"   *Змінити глобальні налаштування можна командами:* `{'/toggletext'}`, `{'/togglevoice'}`, `{'/togglemode'}` (краще в приватному чаті).\n\n"
        f"   🧹 **Очищення історії:**\n" # <-- Добавлено описание clear
        f"   `{'/clear'}` - Очистити *вашу* історію в цьому чаті\n"
        f"   `{'/clear <число>'}` - Очистити *ваші* останні N повідомлень"
    )

    # --- 2. Group Settings (If applicable) ---
    public_group_settings_text = ""
    admin_group_settings_text = ""

    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        # Используем get_group_by_telegram_id, как в clear_handler
        group = await dao.get_group_by_telegram_id(telegram_chat_id=chat.id)
        if group:
            # --- 2a. Public Group Info (Visible to everyone in the group) ---
            group_text_status = f"✅ *Увімкнено*" if group.responds_to_text else f"❌ *Вимкнено*"
            group_voice_status = f"✅ *Увімкнено*" if group.responds_to_voice else f"❌ *Вимкнено*"
            public_group_settings_text = (
                f"\n\n🏢 **Налаштування для цієї групи ('{group.name}') (Загальні):**\n"
                f"   - Відповіді бота на текст: {group_text_status}\n"
                f"   - Обробка ботом голосу: {group_voice_status}"
            )

            # --- 2b. Admin Group Info (Visible only to admins/owners) ---
            is_admin = await is_user_group_admin(chat, user.telegram_id) # Передаем Telegram ID
            if is_admin:
                admin_group_settings_text = (
                    f"\n\n🔑 **Налаштування для групи (Адміністраторам):**\n"
                    f"   *Ви можете змінити налаштування групи командами:*\n"
                    f"   `{'/togglegrouptext'}` - Увімк./Вимк. відповіді на текст\n"
                    f"   `{'/togglegroupvoice'}` - Увімк./Вимк. обробку голосу\n"
                    # <-- Добавлено описание group clear для админов
                    f"\n   *Очищення історії групи (тільки адміни):*\n"
                    f"   `{'/clear group'}` - Очистити *всю* історію групи\n"
                    f"   `{'/clear group <число>'}` - Очистити останні N повідомлень *групи*"
                )

        else:
            public_group_settings_text = "\n\n⚠️ Не вдалося отримати детальні налаштування для цієї групи з бази даних."

    # --- 3. Combine and Send ---
    full_settings_text = user_settings_text + public_group_settings_text + admin_group_settings_text
    try:
        # Добавляем disable_web_page_preview=True, чтобы команды не создавали превью
        await message.answer(full_settings_text, parse_mode="Markdown", disable_web_page_preview=True)
    except TelegramBadRequest as e:
        logger.error(f"Failed to send settings message (Markdown error?): {e}. Text: {full_settings_text}")
        try:
            await message.answer(full_settings_text, disable_web_page_preview=True)
        except Exception as fallback_e:
            logger.error(f"Failed to send settings message even without Markdown: {fallback_e}")
            await message.answer("Не вдалося відобразити налаштування.")


@router.message(filters.Command("toggletext"))
async def toggle_text_response_handler(message: Message, dao: AsyncDAO, user: User) -> None:
    """Переключает настройку ответа на текст."""
    if message.chat.type != ChatType.PRIVATE:
         await message.answer("ℹ️ Зверніть увагу: ця команда змінює ваші глобальні налаштування для всіх чатів.")

    new_value = not user.responds_to_text
    success = await dao.update_user_settings(user_id=user.id, responds_to_text=new_value)

    if success:
        user.responds_to_text = new_value
        log_message = f"User {user.telegram_id} toggled responds_to_text to {user.responds_to_text}"
        status = "*увімкнено*" if user.responds_to_text else "*вимкнено*"
        reply_text = f"✅ Відповіді на текстові повідомлення тепер {status}."
        await log_and_reply(message, log_message, reply_text)
    else:
        logger.error(f"Failed to update responds_to_text for user {user.telegram_id} in DB.")
        await send_error_message(message, "Не вдалося зберегти налаштування.")

@router.message(filters.Command("togglevoice"))
async def toggle_voice_response_handler(message: Message, dao: AsyncDAO, user: User) -> None:
    """Переключает настройку обработки голоса."""
    if message.chat.type != ChatType.PRIVATE:
         await message.answer("ℹ️ Зверніть увагу: ця команда змінює ваші глобальні налаштування для всіх чатів.")

    new_value = not user.responds_to_voice
    success = await dao.update_user_settings(user_id=user.id, responds_to_voice=new_value)

    if success:
        user.responds_to_voice = new_value
        log_message = f"User {user.telegram_id} toggled responds_to_voice to {user.responds_to_voice}"
        status = "*увімкнено*" if user.responds_to_voice else "*вимкнено*"
        reply_text = f"✅ Обробка голосових повідомлень тепер {status}."
        await log_and_reply(message, log_message, reply_text)
    else:
        logger.error(f"Failed to update responds_to_voice for user {user.telegram_id} in DB.")
        await send_error_message(message, "Не вдалося зберегти налаштування.")

@router.message(filters.Command("togglemode"))
async def toggle_voice_mode_handler(message: Message, dao: AsyncDAO, user: User) -> None:
    """Переключает режим обработки голоса."""
    if message.chat.type != ChatType.PRIVATE:
         await message.answer("ℹ️ Зверніть увагу: ця команда змінює ваші глобальні налаштування для всіх чатів.")

    if not user.responds_to_voice:
        await message.answer(f"Спочатку увімкніть обробку голосових повідомлень командою `{'/togglevoice'}`.", parse_mode="Markdown")
        return

    new_value = not user.transcribe_voice_only
    success = await dao.update_user_settings(user_id=user.id, transcribe_voice_only=new_value)

    if success:
        user.transcribe_voice_only = new_value
        log_message = f"User {user.telegram_id} toggled transcribe_voice_only to {user.transcribe_voice_only}"
        mode = "*тільки транскрибувати*" if user.transcribe_voice_only else "*відповідати на повідомлення*"
        reply_text = f"✅ Режим обробки голосових змінено: бот буде {mode}."
        await log_and_reply(message, log_message, reply_text)
    else:
        logger.error(f"Failed to update transcribe_voice_only for user {user.telegram_id} in DB.")
        await send_error_message(message, "Не вдалося зберегти налаштування.")

@router.message(filters.Command("clear"))
async def clear_history_handler(message: Message, command: filters.CommandObject, dao: AsyncDAO, user: User) -> None:
    """
    Очищает историю сообщений согласно аргументам:
    /clear - Очищает ВАШУ историю в текущем чате (приватном или группе).
    /clear <number> - Очищает последние <number> ВАШИХ сообщений в текущем чате.
    /clear group - (Только группы, только админы) Очищает ВСЮ историю группы.
    /clear group <number> - (Только группы, только админы) Очищает последние <number> сообщений ВСЕЙ группы.
    """
    chat_type = message.chat.type
    args = command.args.split() if command.args else []
    chat_id_for_log = message.chat.id
    user_id_for_log = user.telegram_id # Use Telegram ID for logging clarity

    limit: int | None = None
    target_group_wide: bool = False
    target_description: str = ""
    group_db_id: int | None = None # Internal DB ID for the group

    try:
        # --- Argument Parsing and Validation ---
        if chat_type == ChatType.PRIVATE:
            if len(args) == 0:
                # /clear (private)
                target_description = "вашу особисту історію повідомлень"
            elif len(args) == 1:
                # /clear <number> (private)
                try:
                    limit = int(args[0])
                    if limit <= 0:
                        raise ValueError("Limit must be positive.")
                    target_description = f"останні {limit} ваших особистих повідомлень"
                except ValueError:
                    await send_error_message(message, "Невірний формат. Очікується `/clear` або `/clear <число>` у приватних повідомленнях.")
                    return
            else:
                await send_error_message(message, "Невірний формат. Забагато аргументів для приватного чату.")
                return

        elif chat_type in [ChatType.GROUP, ChatType.SUPERGROUP]:
            # Get group from DB first
            group = await dao.get_group_by_telegram_id(telegram_chat_id=message.chat.id)
            if not group:
                logger.warning(f"User {user_id_for_log} tried /clear in group {chat_id_for_log}, but group not found in DB.")
                await message.answer("⚠️ Не вдалося знайти цей чат у базі даних. Будь ласка, спробуйте відправити звичайне повідомлення боту в цій групі, щоб він її зареєстрував.")
                return
            group_db_id = group.id # Store internal group ID

            if len(args) == 0:
                # /clear (group) -> clear user's messages in this group
                target_description = f"ваші повідомлення у групі '{group.name}'"
            elif len(args) == 1:
                if args[0].lower() == "group":
                    # /clear group -> clear all messages in this group (admin only)
                    target_group_wide = True
                    target_description = f"всі повідомлення у групі '{group.name}'"
                else:
                    # /clear <number> (group) -> clear user's last N messages in this group
                    try:
                        limit = int(args[0])
                        if limit <= 0:
                            raise ValueError("Limit must be positive.")
                        target_description = f"останні {limit} ваших повідомлень у групі '{group.name}'"
                    except ValueError:
                        await send_error_message(message, "Невірний формат. Очікується `/clear`, `/clear <число>`, `/clear group` або `/clear group <число>`.")
                        return
            elif len(args) == 2:
                if args[0].lower() == "group":
                    # /clear group <number> -> clear last N messages in this group (admin only)
                    target_group_wide = True
                    try:
                        limit = int(args[1])
                        if limit <= 0:
                            raise ValueError("Limit must be positive.")
                        target_description = f"останні {limit} повідомлень у групі '{group.name}'"
                    except ValueError:
                        await send_error_message(message, "Невірний формат. Очікується `/clear group <число>` (число повинно бути позитивним).")
                        return
                else:
                    await send_error_message(message, "Невірний формат. Очікується `/clear group <число>`.")
                    return
            else:
                await send_error_message(message, "Невірний формат. Забагато аргументів.")
                return

            # --- Permission Check (for group-wide clearing) ---
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

        # --- Perform Deletion ---
        logger.info(f"User {user_id_for_log} requested history clear: chat_type={chat_type}, args={args}, target_group_wide={target_group_wide}, limit={limit}, group_db_id={group_db_id}")

        deleted_count = await dao.clear_history(
            user_id=user.id if not target_group_wide else None, # Pass user.id only if clearing user-specific
            group_id=group_db_id, # Pass internal group ID if in group, else None (handled by logic above)
            clear_group_wide=target_group_wide,
            limit=limit
        )

        # --- Send Confirmation ---
        await message.answer(f"🗑 Історію очищено (видалено {deleted_count} повідомлень: {target_description}).")

    except ValueError as ve: # Catch specific ValueErrors from DAO or parsing
        logger.warning(f"ValueError during /clear for user {user_id_for_log} in chat {chat_id_for_log}: {ve}")
        await send_error_message(message, f"Помилка обробки команди: {ve}")
    except Exception as e:
        logger.error(f"Handler error during history clear for user {user_id_for_log} in chat {chat_id_for_log}: {e}", exc_info=True)
        await send_error_message(message, "Сталася неочікувана помилка під час очищення історії.")
# --- Message handlers ---

async def get_group_or_none(dao: AsyncDAO, chat: Chat) -> Group | None:
    """Вспомогательная функция для получения объекта Group или None."""
    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        try:
            chat_title = cast(str, chat.title) if chat.title else f"Group_{chat.id}"
            group = await dao.get_or_create_group(telegram_chat_id=chat.id, name=chat_title)
            return group
        except Exception as e:
            logger.error(f"Failed to get or create group for chat_id {chat.id}: {e}", exc_info=True)
            return None
    return None

# --- Вспомогательная функция для обработки результата Gemini ---
async def handle_gemini_result(
    gemini_result: Dict[str, Any],
    message: Message,
    dao: AsyncDAO,
    user: User,
    group_db_id: int | None
) -> None:
    """Обрабатывает структурированный ответ от Gemini API."""
    chat = message.chat
    result_type = gemini_result.get("type")
    result_data = gemini_result.get("data")

    if result_type == "text":
        response_text = result_data
        if response_text: # Дополнительная проверка, что текст не пустой
            logger.info(f"Gemini returned text for user {user.telegram_id} in chat {chat.id}. Saving and replying.")
            # 4. Сохраняем ответ модели
            await dao.add_message(
                user_id=user.id, role=MessageRole.MODEL, text=response_text, group_id=group_db_id
            )
            logger.debug(f"Model response queued for save (user {user.telegram_id}, group_id {group_db_id})")

            # 5. Отправляем ответ пользователю
            reply_method = message.reply if chat.type != ChatType.PRIVATE else message.answer
            response_lines = response_text.split("\\n")
            full_response_sent = ""
            for line in response_lines:
                line = line.lstrip()
                if line.strip():
                    try:
                         await reply_method(line, parse_mode="Markdown")
                         full_response_sent += line + "\n"
                         await asyncio.sleep(0.1)
                    except TelegramBadRequest as e:
                         logger.warning(f"Failed to send part of response (Markdown) to {chat.id}: {e}. Content: '{line[:50]}...'")
                         try:
                             await reply_method(line, parse_mode=None)
                             full_response_sent += line + "\n"
                         except Exception as inner_e:
                             logger.error(f"Failed to send part of response (no Markdown) to {chat.id}: {inner_e}")
                             break
                    except (TelegramNetworkError, Exception) as e:
                         logger.error(f"Error sending part of response to {chat.id}: {e}", exc_info=True)
                         break
            logger.debug(f"Finished sending response to chat {chat.id}. Approx length {len(full_response_sent)}")
        else:
             logger.warning(f"Gemini result type was 'text' but data was empty for user {user.telegram_id} in chat {chat.id}.")
             await send_error_message(message, "AI повернув порожню відповідь.")


    elif result_type == "function_call":
        function_name = result_data.get("name")
        # function_args = result_data.get("args", {}) # Аргументы пока не используются

        if function_name == "do_not_respond":
            logger.info(f"Function call '{function_name}' received for user {user.telegram_id} in chat {chat.id}. No reply sent.")
            # Ничего не делаем

        elif function_name == "disable_responses":
            logger.info(f"Function call '{function_name}' received. Disabling text responses for user {user.telegram_id}.")
            success = await dao.update_user_settings(user_id=user.id, responds_to_text=False)
            if success:
                user.responds_to_text = False # Обновляем в памяти
                await message.answer("⛔️ Я більше не буду відповідати на ваші текстові повідомлення за вашим запитом.")
            else:
                logger.error(f"Failed to disable responses for user {user.telegram_id} via DAO.")
                await send_error_message(message, "Не вдалося вимкнути відповіді. Спробуйте пізніше.")
        else:
            # Неизвестный вызов функции
             logger.warning(f"Received unknown function call '{function_name}' from Gemini.")
             # Можно отправить сообщение или проигнорировать


    elif result_type == "no_response":
        reason = result_data if isinstance(result_data, str) else "Reason not specified"
        logger.info(f"Gemini returned no response for user {user.telegram_id} in chat {chat.id}. Reason: {reason}")
        # Решаем, нужно ли уведомлять пользователя. Возможно, нет, если это do_not_respond или safety.
        # await send_error_message(message, "AI вирішив не відповідати або відповідь була заблокована.")

    elif result_type == "error":
        error_msg = result_data if isinstance(result_data, str) else "Unknown Gemini error"
        logger.error(f"Gemini API error for user {user.telegram_id} in chat {chat.id}: {error_msg}")
        await send_error_message(message, "Помилка під час звернення до AI. Спробуйте пізніше.")

    else:
        # Неизвестный тип результата
        logger.error(f"Received unknown result type from Gemini: {result_type}. Data: {result_data}")
        await send_error_message(message, "Отримано незрозумілий результат від AI.")


@router.message(F.text)
async def text_handler(message: Message, dao: AsyncDAO, user: User) -> None:
    """Обрабатывает текстовые сообщения, проверяя настройки пользователя и группы."""
    chat = message.chat
    group = await get_group_or_none(dao, chat) # Fetches/creates group if needed
    group_db_id = group.id if group else None

    # --- MODIFIED Check: Check user AND group settings ---
    if not user.responds_to_text:
        logger.debug(f"Ignoring text message from user {user.telegram_id} in chat {chat.id} due to USER settings.")
        return
    if group and not group.responds_to_text:
         logger.debug(f"Ignoring text message from user {user.telegram_id} in group chat {chat.id} (DB ID: {group.id}) due to GROUP settings.")
         return
    # --- End of MODIFIED Check ---

    if not message.text: # Should not happen with F.text but good practice
        logger.debug(f"Ignoring message without text content from user {user.telegram_id} in chat {chat.id}")
        return

    logger.info(f"Processing text message from user {user.telegram_id} in chat {chat.id} (type: {chat.type}, group_id: {group_db_id})")
    try:
        await message.bot.send_chat_action(chat_id=chat.id, action="typing")
    except (TelegramNetworkError, TelegramBadRequest, TelegramForbiddenError) as e:
         logger.warning(f"Failed to send chat action 'typing' to {chat.id}: {e}")

    try:
        # Handle replies (no change needed here)
        text_to_save = message.text
        if (message.reply_to_message
                and message.reply_to_message.from_user # Ensure reply is to a user message
                and not message.reply_to_message.from_user.is_bot # Optional: don't format replies to bots?
                and message.reply_to_message.text
                and not message.reply_to_message.audio
                and not message.reply_to_message.voice):
            original_sender = message.reply_to_message.from_user.first_name or f"User_{message.reply_to_message.from_user.id}"
            original_text = message.reply_to_message.text
            reply_text = message.text
            # Make format clearer for the model
            text_to_save = f"User replied: '{reply_text}'\nTo the message from {original_sender}: '{original_text}'"
            logger.debug(f"Formatted reply text for saving: {text_to_save[:100]}...")

        # 1. Сохраняем сообщение пользователя
        await dao.add_message(
            user_id=user.id, role=MessageRole.USER, text=text_to_save, group_id=group_db_id
        )
        logger.debug(f"User message queued for save (user {user.telegram_id}, group_id {group_db_id})")

        # 2. Получаем историю
        if group_db_id is not None:
            message_history = await dao.get_group_messages_as_contents(group_id=group_db_id)
        else:
            message_history = await dao.get_user_private_messages_as_contents(user_id=user.id)
        logger.debug(f"Fetched {len(message_history)} messages for context (user {user.telegram_id}, group_id {group_db_id})")

        # Check if history is empty (maybe first message after clear)
        if not message_history:
            logger.warning(f"Message history is empty before calling Gemini for user {user.telegram_id}, group_id {group_db_id}. This might happen after /clear.")
            # Decide if you want to proceed or inform the user
            # await send_error_message(message, "Історія повідомлень порожня. Не можу створити відповідь без контексту.")
            # return # Or proceed, Gemini might handle it

        # 3. Получаем ответ от AI
        gemini_result = await get_text_response(message_history=message_history, user=user)

        # 4. Обрабатываем результат Gemini (handle_gemini_result handles sending/saving model response)
        await handle_gemini_result(gemini_result, message, dao, user, group_db_id)

    except Exception as e:
        logger.error(f"Handler error processing text message for user {user.telegram_id} in chat {chat.id}: {e}", exc_info=True)
        await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки вашого текстового повідомлення.")


# --- MODIFIED Voice Handler ---
@router.message(F.voice)
async def voice_handler(message: Message, dao: AsyncDAO, user: User) -> None:
    """Обрабатывает голосовые сообщения, проверяя настройки пользователя и группы."""
    chat = message.chat
    group = await get_group_or_none(dao, chat)
    group_db_id = group.id if group else None

    # --- MODIFIED Check: Check user AND group settings ---
    if not user.responds_to_voice:
        logger.debug(f"Ignoring voice message from user {user.telegram_id} in chat {chat.id} due to USER settings.")
        return
    if group and not group.responds_to_voice:
        logger.debug(f"Ignoring voice message from user {user.telegram_id} in group chat {chat.id} (DB ID: {group.id}) due to GROUP settings.")
        return
    # --- End of MODIFIED Check ---

    if not message.voice: # Should not happen with F.voice
        logger.warning(f"Voice message object is missing for user {user.telegram_id} in chat {chat.id}")
        return

    logger.info(f"Processing voice message from user {user.telegram_id} in chat {chat.id} (type: {chat.type}, group_id: {group_db_id})")
    try:
        # Use record_voice for voice, or typing as fallback
        await message.bot.send_chat_action(chat_id=chat.id, action="typing")
    except (TelegramNetworkError, TelegramBadRequest, TelegramForbiddenError) as e:
         logger.warning(f"Failed to send chat action 'record_voice' to {chat.id}: {e}. Falling back to 'typing'.")
         try:
             await message.bot.send_chat_action(chat_id=chat.id, action="typing")
         except Exception as inner_e:
              logger.warning(f"Failed to send fallback chat action 'typing' to {chat.id}: {inner_e}")


    voice = message.voice
    audio_bytes: bytes | None = None

    # --- Download Voice File (no change needed here) ---
    try:
        file = await message.bot.get_file(voice.file_id)
        if not file.file_path:
            logger.error(f"File path is missing for voice file_id={voice.file_id}")
            await send_error_message(message, "Помилка: не вдалося отримати шлях до файлу.")
            return
        # Consider using BytesIO for memory efficiency with large files if needed
        downloaded_file = await message.bot.download_file(file.file_path)
        if downloaded_file is None: # Check if download actually returned something
            logger.error(f"Failed to download voice file from path={file.file_path}, received None.")
            await send_error_message(message, "Помилка: не вдалося завантажити голосове повідомлення (отримано None).")
            return
        audio_bytes = downloaded_file.read()
        logger.debug(f"Downloaded {len(audio_bytes)} bytes for voice message from user {user.telegram_id} in chat {chat.id}")
    except (TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError) as e:
        logger.error(f"Telegram API error downloading voice file: {e}", exc_info=True)
        await send_error_message(message, f"Помилка мережі або API Telegram під час завантаження: {e}.")
        return
    except Exception as e:
        logger.error(f"Unexpected error downloading voice file: {e}", exc_info=True)
        await send_error_message(message, "Несподівана помилка при завантаженні файлу.")
        return

    if not audio_bytes: # Double check after download block
        logger.error(f"Audio data is empty after download attempt for user {user.telegram_id}")
        # Avoid sending another message if one was already sent above
        # await send_error_message(message, "Не вдалося отримати дані голосового повідомлення.")
        return

    # --- Process Voice with AI ---
    try:
        # 1. Сохраняем голосовое
        await dao.add_message(
            user_id=user.id, role=MessageRole.USER, audio_data=audio_bytes, group_id=group_db_id
        )
        logger.debug(f"User voice message queued for save (user {user.telegram_id}, group_id {group_db_id})")

        # 2. Получаем историю
        if group_db_id is not None:
            message_history = await dao.get_group_messages_as_contents(group_id=group_db_id)
        else:
            message_history = await dao.get_user_private_messages_as_contents(user_id=user.id)
        logger.debug(f"Fetched {len(message_history)} messages for context (user {user.telegram_id}, group_id {group_db_id})")

        if not message_history:
             logger.warning(f"Message history is empty before calling Gemini for user {user.telegram_id}, group_id {group_db_id} (voice).")
             # Decide action - maybe just transcribe if history is empty?

        # Определяем режим работы AI (пользовательский режим 'transcribe_only' все еще имеет приоритет)
        generate_full_response = not user.transcribe_voice_only
        logger.debug(f"Calling AI for voice. Generate response based on user setting: {generate_full_response} (user {user.telegram_id}, group_id {group_db_id})")

        # 3. Получаем ответ/транскрипцию от AI
        gemini_result = await get_audio_response(
            message_history=message_history, # History now includes the audio Part
            user=user,
            response=generate_full_response # Use user's preference for response/transcription
        )

        # 4. Обрабатываем результат Gemini
        await handle_gemini_result(gemini_result, message, dao, user, group_db_id)

    except Exception as e:
        logger.error(f"Handler error processing voice message for user {user.telegram_id} in chat {chat.id}: {e}", exc_info=True)
        await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки вашого голосового повідомлення.")
