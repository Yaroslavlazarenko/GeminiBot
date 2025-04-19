import logging
from typing import Optional, Dict, Any
import asyncio

from aiogram.types import Message, Chat
from aiogram.enums import ChatType, ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError

from database.models import User, Group, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO

logger = logging.getLogger(__name__)

# Define Telegram's approximate message length limit
TELEGRAM_MAX_MESSAGE_LENGTH = 4000 # Use a safe limit slightly below 4096

async def handle_gemini_result(
    gemini_result: Dict[str, Any],
    message: Message,
    message_dao: MessageHistoryDAO,
    user_dao: UserDAO,
    user: User,
    group_db_id: int | None
) -> None:
    """
    Обрабатывает структурированный ответ от Gemini API.
    Поддерживает обработку команд найденных в тексте ответа.
    """
    chat = message.chat
    result_type = gemini_result.get("type")
    result_data = gemini_result.get("data")

    if result_type == "json_response":
        # Сначала отправляем текст, если он есть
        if "text" in result_data and result_data["text"].strip():
            response_text = result_data["text"].strip()
            
            # Сохраняем ответ в базу
            logger.info(f"Gemini returned text for user {user.telegram_id} in chat {chat.id}.")
            await message_dao.add_message(
                user_id=user.id, role=MessageRole.MODEL, text=response_text, group_id=group_db_id
            )
            logger.debug(f"Model response queued for save (user {user.telegram_id}, group_id {group_db_id})")

            # Разбиваем на части по маркеру \n
            response_lines = response_text.split("\\n")
            full_response_sent = ""
            is_first_message_part_sent = False

            for line in response_lines:
                line = line.lstrip()
                if not line.strip():
                    continue

                line_chunks = [line[i:i+TELEGRAM_MAX_MESSAGE_LENGTH] for i in range(0, len(line), TELEGRAM_MAX_MESSAGE_LENGTH)]

                for chunk in line_chunks:
                    if not chunk.strip():
                        continue

                    if not is_first_message_part_sent and chat.type != ChatType.PRIVATE:
                        send_method = message.reply
                        method_name = "reply"
                    else:
                        send_method = message.answer
                        method_name = "answer"

                    try:
                        await send_method(chunk, parse_mode="HTML")
                        full_response_sent += chunk
                        if not is_first_message_part_sent:
                            is_first_message_part_sent = True
                        await asyncio.sleep(0.1)
                    except TelegramBadRequest as e:
                        logger.warning(f"Failed to send chunk ({method_name}, HTML) to {chat.id}: {e}. Content: '{chunk[:50]}...'. Retrying without HTML.")
                        try:
                            if not is_first_message_part_sent and chat.type != ChatType.PRIVATE:
                                send_method_fallback = message.reply
                            else:
                                send_method_fallback = message.answer

                            await send_method_fallback(chunk, parse_mode=None)
                            full_response_sent += chunk
                            if not is_first_message_part_sent:
                                is_first_message_part_sent = True
                            await asyncio.sleep(0.1)
                        except Exception as inner_e:
                            logger.error(f"Failed to send chunk ({method_name}, no HTML) to {chat.id}: {inner_e}. Stopping message sending for this response.")
                            return
                    except Exception as e:
                        logger.error(f"Unexpected error sending chunk ({method_name}) to {chat.id}: {e}")
                        return
            logger.debug(f"Finished sending response to chat {chat.id}. Approx length {len(full_response_sent)}")

        # После отправки текста обрабатываем команды
        for command in result_data.get("commands", []):
            command_name = command.get("name")
            command_args = command.get("args", {})
            
            if command_name == "do_not_respond":
                logger.info(f"Found do_not_respond command for user {user.telegram_id}")
                return
            elif command_name == "disable_responses":
                logger.info(f"Found disable_responses command for user {user.telegram_id}")
                success = await user_dao.update_user_settings(user_id=user.id, responds_to_text=False)
                if success:
                    user.responds_to_text = False
                    # Не отправляем дополнительное сообщение, если уже был текст в ответе
                    if not result_data.get("text", "").strip():
                        await message.answer("⛔️ Я більше не буду відповідати на ваші текстові повідомлення за вашим запитом.")
                else:
                    logger.error(f"Failed to disable responses for user {user.telegram_id}")
                    if not result_data.get("text", "").strip():
                        await send_error_message(message, "Не вдалося вимкнути відповіді. Спробуйте пізніше.")
            elif command_name == "add_reaction":
                emoji = command_args.get("emoji")
                if emoji:
                    try:
                        await message.react([{"type": "emoji", "emoji": emoji}])
                        logger.info(f"Added reaction {emoji} to message from user {user.telegram_id} in chat {chat.id}")
                    except Exception as e:
                        logger.error(f"Failed to add reaction {emoji} to message in chat {chat.id}: {e}")

    elif result_type == "error":
        error_msg = result_data if isinstance(result_data, str) else "Unknown Gemini error"
        logger.error(f"Gemini API error for user {user.telegram_id} in chat {chat.id}: {error_msg}")
        await send_error_message(message, "Помилка під час звернення до AI. Спробуйте пізніше.")

async def is_user_group_admin(chat: Chat, user_id: int) -> bool:
    """Checks if a user is an administrator or owner in a group/supergroup."""
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return False
    try:
        member = await chat.get_member(user_id)
        return member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]
    except TelegramBadRequest as e:
        # Handle common cases like "user not found" or "chat not found" gracefully
        if "user not found" in str(e) or "chat not found" in str(e):
             logger.info(f"Could not get member status for user {user_id} in chat {chat.id}: {e}")
        else:
             logger.warning(f"Could not get member status for user {user_id} in chat {chat.id}: {e}")
        return False
    except TelegramForbiddenError:
        logger.warning(f"Bot is forbidden from getting member status in chat {chat.id}. Cannot verify admin.")
        return False
    except Exception as e:
        logger.error(f"Unexpected error checking admin status for user {user_id} in chat {chat.id}: {e}", exc_info=True)
        return False

async def send_error_message(message: Message, error_text: str) -> None:
    """Sends an error message to the user/chat."""
    try:
        await message.answer(error_text, parse_mode="HTML")
    except TelegramBadRequest as e:
        logger.warning(f"Failed to send error message (HTML) to chat {message.chat.id}: {e}. Retrying without HTML.")
        try:
             await message.answer(error_text, parse_mode=None)
        except Exception as inner_e:
             logger.error(f"Failed to send error message (no HTML) to chat {message.chat.id}: {inner_e}", exc_info=True)
    except (TelegramNetworkError, TelegramForbiddenError) as e:
        logger.error(f"Network/Forbidden error sending error message to chat {message.chat.id}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error sending error message to chat {message.chat.id}: {e}", exc_info=True)

async def log_and_reply(message: Message, log_message: str, reply_text: str, level: int = logging.INFO) -> None:
    """Logs a message and sends a reply to the user/chat."""
    logger.log(level, log_message)
    try:
        await message.answer(reply_text, parse_mode="HTML")
    except TelegramBadRequest as e:
        logger.warning(f"Failed to send reply message (HTML) to chat {message.chat.id}: {e}. Retrying without HTML.")
        try:
            await message.answer(reply_text, parse_mode=None)
        except Exception as inner_e:
             logger.error(f"Failed to send reply message (no HTML) to chat {message.chat.id}: {inner_e}", exc_info=True)
    except (TelegramNetworkError, TelegramForbiddenError) as e:
        logger.error(f"Network/Forbidden error sending reply message to chat {message.chat.id}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error sending reply message to chat {message.chat.id}: {e}", exc_info=True)

async def get_group_or_none(group_dao: GroupDAO, chat: Chat) -> Optional[Group]:
    """Gets group by Telegram chat ID using GroupDAO, returns None if not found or on error."""
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return None
    try:
        group = await group_dao.get_or_create_group(chat.id, chat.title)
        if not group:
            # It's often normal for a group not to be in the DB yet, so INFO level might be better
            logger.info(f"Group with telegram_chat_id={chat.id} not found in DB.")
            return None
        return group
    except Exception as e:
        logger.error(f"Error getting group {chat.id} from DB: {e}", exc_info=True)
        return None