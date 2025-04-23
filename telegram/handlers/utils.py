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

def escape_quotes(text: str) -> str:
    """
    Escapes double quotes in text while preserving HTML tags.
    Only escapes quotes within the text content, not in the JSON structure.
    """
    if not text:
        return text
        
    # Split the text into parts that are inside HTML tags and parts that are not
    parts = []
    current_pos = 0
    while current_pos < len(text):
        # Find the next HTML tag
        tag_start = text.find('<', current_pos)
        if tag_start == -1:
            # No more tags, process the rest of the text
            parts.append(('text', text[current_pos:]))
            break
            
        # Process text before the tag
        if tag_start > current_pos:
            parts.append(('text', text[current_pos:tag_start]))
            
        # Find the end of the tag
        tag_end = text.find('>', tag_start)
        if tag_end == -1:
            # Malformed HTML, process the rest as text
            parts.append(('text', text[tag_start:]))
            break
            
        # Add the tag
        parts.append(('tag', text[tag_start:tag_end + 1]))
        current_pos = tag_end + 1
    
    # Process the parts
    result = []
    for part_type, content in parts:
        if part_type == 'text':
            # Escape quotes in text parts
            result.append(content.replace('"', '\\"'))
        else:
            # Keep HTML tags as is
            result.append(content)
    
    return ''.join(result)

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

    # Initialize response_data with default values
    response_data = {
        "text": "",
        "commands": result_data.get("commands", []) if result_data else []
    }

    if result_type == "json_response":
        # Отправляем текст, если он есть
        if "text" in result_data and result_data["text"].strip():
            # Update response_data with the text
            response_data["text"] = result_data["text"].strip().replace('"', '\\"')
            response_text = response_data["text"]
            logger.info(f"Gemini returned text for user {user.telegram_id} in chat {chat.id}.")
            
            # Look for reply_to_message command first
            reply_to_id = None
            for command in response_data.get("commands", []):
                if command.get("name") == "reply_to_message":
                    reply_to_id = command.get("args", {}).get("message_id")
                    break

            # Определяем метод отправки
            send_method = message.answer if chat.type == ChatType.PRIVATE else message.reply
            method_name = "answer" if chat.type == ChatType.PRIVATE else "reply"
            method_kwargs = {"text": response_text, "parse_mode": "HTML"}
            
            # If we need to reply to a specific message, add reply_to_message_id
            if reply_to_id:
                method_kwargs["reply_to_message_id"] = reply_to_id
                method_name = "reply_to_specific"

            try:
                sent_message = await send_method(**method_kwargs)
                await message_dao.add_message(
                    user_id=user.id, 
                    role=MessageRole.MODEL, 
                    text=response_text, 
                    group_id=group_db_id,
                    telegram_message_id=sent_message.message_id
                )
            except TelegramBadRequest as e:
                # If sending with HTML fails or message not found, try without HTML
                logger.warning(f"Failed to send message ({method_name}) to {chat.id}: {e}. Content: '{response_text[:50]}...'. Retrying without HTML.")
                try:
                    method_kwargs["parse_mode"] = None
                    # If message not found error, remove reply_to
                    if "message to reply not found" in str(e).lower():
                        if "reply_to_message_id" in method_kwargs:
                            del method_kwargs["reply_to_message_id"]
                            logger.warning(f"Message {reply_to_id} not found in chat {chat.id}, falling back to default reply")
                    sent_message = await send_method(**method_kwargs)
                    await message_dao.add_message(
                        user_id=user.id, 
                        role=MessageRole.MODEL, 
                        text=response_text, 
                        group_id=group_db_id,
                        telegram_message_id=sent_message.message_id
                    )
                except Exception as inner_e:
                    logger.error(f"Failed to send message ({method_name}, no HTML) to {chat.id}: {inner_e}.")
                    return
            except Exception as e:
                logger.error(f"Unexpected error sending message ({method_name}) to {chat.id}: {e}")
                return

            logger.debug(f"Finished sending response to chat {chat.id}. Length: {len(response_text)}")

        # После отправки текста обрабатываем остальные команды
        for command in response_data.get("commands", []):
            command_name = command.get("name")
            command_args = command.get("args", {})
            
            if command_name == "do_not_respond":
                logger.info(f"Found do_not_respond command for user {user.telegram_id}")
                return
            elif command_name == "disable_responses":
                logger.info(f"Found disable_responses command for user {user.telegram_id}")
                success = await user_dao.update_user_settings(user_id=user.id, is_global_disabled=True)
                if success:
                    user.is_global_disabled = True
                    # Не отправляем дополнительное сообщение, если уже был текст в ответе
                    if not response_data.get("text", "").strip():
                        await message.answer("⛔️ Я більше не буду відповідати на ваші повідомлення за вашим запитом.")
                else:
                    logger.error(f"Failed to disable responses for user {user.telegram_id}")
                    if not response_data.get("text", "").strip():
                        await send_error_message(message, "Не вдалося вимкнути відповіді. Спробуйте пізніше.")
            elif command_name == "add_reaction":
                emoji = command_args.get("emoji")
                message_ids = command_args.get("message_ids", [])
                
                if not emoji:
                    logger.warning(f"Missing emoji in add_reaction command for user {user.telegram_id}")
                    return
                
                chat = message.chat
                reaction = [{"type": "emoji", "emoji": emoji}]
                
                # Если message_ids не указан, используем ID текущего сообщения
                if not message_ids:
                    message_ids = [message.message_id]
                    logger.info(f"Using current message ID {message.message_id} for reaction from user {user.telegram_id} in chat {chat.id}")
                
                for msg_id in message_ids:
                    try:
                        await message.bot.set_message_reaction(
                            chat_id=chat.id,
                            message_id=msg_id,
                            reaction=reaction
                        )
                        logger.info(f"Added reaction {emoji} to message {msg_id} from user {user.telegram_id} in chat {chat.id}")
                    except Exception as e:
                        logger.error(f"Failed to add reaction {emoji} to message {msg_id} in chat {chat.id}: {e}")
            # reply_to_message обрабатывается выше, при отправке сообщения

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