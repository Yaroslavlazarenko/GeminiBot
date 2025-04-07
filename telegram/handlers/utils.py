import logging
from typing import Optional, Dict, Any
import asyncio

from aiogram.types import Message, Chat
from aiogram.enums import ChatType, ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError

from database.models import User, Group, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO

logger = logging.getLogger(__name__)


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
    В группах первая строка ответа отправляется как reply, последующие - как answer.
    В личных чатах все строки отправляются как answer.
    """
    chat = message.chat
    result_type = gemini_result.get("type")
    result_data = gemini_result.get("data")

    if result_type == "text":
        response_text = result_data
        if response_text:
            logger.info(f"Gemini returned text for user {user.telegram_id} in chat {chat.id}. Saving and replying/answering.")
            await message_dao.add_message(
                user_id=user.id, role=MessageRole.MODEL, text=response_text, group_id=group_db_id
            )
            logger.debug(f"Model response queued for save (user {user.telegram_id}, group_id {group_db_id})")

            response_lines = response_text.split("\\n")
            full_response_sent = ""
            is_first_line = True

            for line in response_lines:
                line = line.lstrip()
                if line.strip():
                    if is_first_line and chat.type != ChatType.PRIVATE:
                        send_method = message.reply
                        method_name = "reply"
                    else:
                        send_method = message.answer
                        method_name = "answer"

                    try:
                        await send_method(line, parse_mode="Markdown")
                        full_response_sent += line + "\n"
                        if is_first_line:
                            is_first_line = False
                        await asyncio.sleep(0.1)
                    except TelegramBadRequest as e:
                        logger.warning(f"Failed to send part of response ({method_name}, Markdown) to {chat.id}: {e}. Content: '{line[:50]}...'")
                        try:
                            await send_method(line, parse_mode=None)
                            full_response_sent += line + "\n"
                            if is_first_line:
                                is_first_line = False
                        except Exception as inner_e:
                            logger.error(f"Failed to send part of response ({method_name}, no Markdown) to {chat.id}: {inner_e}. Stopping message sending.")
                            break
                    except (TelegramNetworkError, TelegramForbiddenError, Exception) as e:
                        logger.error(f"Error sending part of response ({method_name}) to {chat.id}: {e}", exc_info=True)
                        break

            logger.debug(f"Finished sending response to chat {chat.id}. Approx length {len(full_response_sent)}")
        else:
             logger.warning(f"Gemini result type was 'text' but data was empty for user {user.telegram_id} in chat {chat.id}.")
             await send_error_message(message, "AI повернув порожню відповідь.")

    elif result_type == "function_call":
        function_name = result_data.get("name")
        if function_name == "do_not_respond":
            logger.info(f"Function call '{function_name}' received for user {user.telegram_id} in chat {chat.id}. No reply sent.")
        elif function_name == "disable_responses":
            logger.info(f"Function call '{function_name}' received. Disabling text responses for user {user.telegram_id}.")
            success = await user_dao.update_user_settings(user_id=user.id, responds_to_text=False)
            if success:
                user.responds_to_text = False
                await message.answer("⛔️ Я більше не буду відповідати на ваші текстові повідомлення за вашим запитом.")
            else:
                logger.error(f"Failed to disable responses for user {user.telegram_id} via DAO.")
                await send_error_message(message, "Не вдалося вимкнути відповіді. Спробуйте пізніше.")
        else:
             logger.warning(f"Received unknown function call '{function_name}' from Gemini.")

    elif result_type == "no_response":
        reason = result_data if isinstance(result_data, str) else "Reason not specified"
        logger.info(f"Gemini returned no response for user {user.telegram_id} in chat {chat.id}. Reason: {reason}")
    elif result_type == "error":
        error_msg = result_data if isinstance(result_data, str) else "Unknown Gemini error"
        logger.error(f"Gemini API error for user {user.telegram_id} in chat {chat.id}: {error_msg}")
        await send_error_message(message, "Помилка під час звернення до AI. Спробуйте пізніше.")
    else:
        logger.error(f"Received unknown result type from Gemini: {result_type}. Data: {result_data}")
        await send_error_message(message, "Отримано незрозумілий результат від AI.")


async def is_user_group_admin(chat: Chat, user_id: int) -> bool:
    """Checks if a user is an administrator or owner in a group/supergroup."""
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return False
    try:
        member = await chat.get_member(user_id)
        return member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]
    except TelegramBadRequest as e:
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

async def get_group_or_none(group_dao: GroupDAO, chat: Chat) -> Optional[Group]:
    """Gets group by Telegram chat ID using GroupDAO, returns None if not found."""
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return None
    try:
        group = await group_dao.get_group_by_telegram_id(chat.id)
        if not group:
            logger.warning(f"Group with telegram_chat_id={chat.id} not found in DB.")
            return None
        return group
    except Exception as e:
        logger.error(f"Error getting group {chat.id} from DB: {e}", exc_info=True)
        return None