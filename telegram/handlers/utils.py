import logging
from typing import Optional, Dict, Any
import asyncio
import html  # Added missing import
import time
from collections import defaultdict
from aiogram.types import Message, Chat
from aiogram.enums import ChatType, ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError, TelegramRetryAfter

from database.models import User, Group, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO

logger = logging.getLogger(__name__)

# Define Telegram's approximate message length limit
TELEGRAM_MAX_MESSAGE_LENGTH = 4000 # Use a safe limit slightly below 4096

# Rate limiting configuration
_last_edit_time = defaultdict(float)  # {chat_id: last_edit_timestamp}
_edit_intervals = defaultdict(float)  # {chat_id: current_interval}
BASE_INTERVAL = 1.0  # Base interval between edits in seconds
MAX_INTERVAL = 5.0   # Maximum interval between edits

async def rate_limited_edit(message, **kwargs):
    """
    Edits a message with rate limiting and exponential backoff.
    Handles TelegramRetryAfter exceptions automatically.
    """
    chat_id = message.chat.id
    current_time = time.time()
    
    # Calculate wait time based on previous edit
    elapsed = current_time - _last_edit_time[chat_id]
    if elapsed < _edit_intervals[chat_id]:
        wait_time = _edit_intervals[chat_id] - elapsed
        await asyncio.sleep(wait_time)
    
    while True:
        try:
            result = await message.edit_text(**kwargs)
            # Success - reduce interval for next time
            _edit_intervals[chat_id] = max(BASE_INTERVAL, _edit_intervals[chat_id] * 0.75)
            _last_edit_time[chat_id] = time.time()
            return result
            
        except TelegramRetryAfter as e:
            # Increase interval for this chat
            _edit_intervals[chat_id] = min(MAX_INTERVAL, _edit_intervals[chat_id] * 2.0)
            logger.warning(f"Rate limit hit for chat {chat_id}, waiting {e.retry_after} seconds")
            await asyncio.sleep(e.retry_after)
            continue
            
        except Exception as e:
            # Other errors - pass through
            raise

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
    group_db_id: Optional[int] # Use Optional for clarity
) -> None:
    """
    Обрабатывает структурированный ответ от Gemini API.
    Поддерживает обработку команд найденных в тексте ответа.
    Исправлена обработка кавычек и HTML-спецсимволов в тексте.
    """
    chat = message.chat
    result_type = gemini_result.get("type")
    result_data = gemini_result.get("data")

    # Initialize response_data with default values
    response_data = {
        "text": "", # Stores the RAW text from Gemini
        "commands": result_data.get("commands", []) if isinstance(result_data, dict) else []
    }
    sent_text_successfully = False # Flag to track if text was sent

    if result_type == "json_response" and isinstance(result_data, dict):
        # Store raw text if it exists
        raw_text_from_gemini = result_data.get("text", "").strip()
        if raw_text_from_gemini:
            response_data["text"] = raw_text_from_gemini # Store raw text
            logger.info(f"Gemini returned text for user {user.telegram_id} in chat {chat.id}. Length: {len(raw_text_from_gemini)}")

            # Escape text specifically for HTML sending
            # This handles < > & correctly. Quotes " are usually fine in HTML content.
            text_for_html_sending = html.escape(raw_text_from_gemini)
            logger.debug(f"Raw text: '{raw_text_from_gemini[:100]}...', HTML escaped: '{text_for_html_sending[:100]}...'")

            # Look for reply_to_message command first
            reply_to_id = None
            for command in response_data.get("commands", []):
                if command.get("name") == "reply_to_message":
                    reply_to_id = command.get("args", {}).get("message_id")
                    # Remove the command after processing it for sending
                    # response_data["commands"].remove(command) # Optional: remove if it shouldn't be processed again
                    break

            # Определяем метод отправки
            send_method = message.answer if chat.type == ChatType.PRIVATE else message.reply
            method_name = "answer" if chat.type == ChatType.PRIVATE else "reply"
            method_kwargs = {"text": text_for_html_sending, "parse_mode": "HTML"}

            # If we need to reply to a specific message, adjust method and args
            if reply_to_id:
                # Prefer reply even in private chats if specific message ID is given
                send_method = message.reply
                method_kwargs["reply_to_message_id"] = reply_to_id
                method_name = "reply_to_specific"
                logger.info(f"Attempting to reply to specific message {reply_to_id} in chat {chat.id}")


            try:
                sent_message = await send_method(**method_kwargs)
                await message_dao.add_message(
                    user_id=user.id,
                    role=MessageRole.MODEL,
                    text=response_data["text"],
                    group_id=group_db_id,
                    telegram_message_id=sent_message.message_id
                )
                sent_text_successfully = True
                logger.debug(f"Successfully sent message ({method_name}, HTML) to chat {chat.id}. Length: {len(text_for_html_sending)}")

            except TelegramBadRequest as e:
                error_text = str(e).lower()
                if "message is not modified" in error_text:
                    # Message content hasn't changed, treat as success
                    logger.info(f"Message content unchanged for chat {chat.id}")
                    sent_text_successfully = True
                    return  # Exit early since no modification needed
                
                logger.warning(f"Failed to send message ({method_name}, HTML) to {chat.id}: {e}. Content (raw): '{response_data['text'][:50]}...'. Retrying without HTML.")
                try:
                    # Prepare arguments for retry without HTML
                    retry_kwargs = {"text": response_data["text"]} # Use RAW text
                    
                    # Check if the error was 'message to reply not found'
                    if "message to reply not found" in str(e).lower() and "reply_to_message_id" in method_kwargs:
                         logger.warning(f"Message {reply_to_id} not found in chat {chat.id} for reply. Sending as regular message/reply.")
                         # Keep send_method as determined earlier (answer/reply) but remove reply_to_message_id
                         # If it was reply_to_specific, method_name stays reply_to_specific for logging clarity
                         # The actual method (message.reply) remains appropriate even without the specific ID in group chats.
                    elif reply_to_id and "reply_to_message_id" in method_kwargs:
                         # If it wasn't "not found" but we had a reply_id, keep it for the retry
                        retry_kwargs["reply_to_message_id"] = reply_to_id

                    # Determine the send method again based on context if needed, or reuse
                    # If original was reply_to_specific, stick to message.reply
                    # Otherwise, use the originally determined send_method
                    retry_send_method = message.reply if method_name == "reply_to_specific" else send_method
                    retry_method_name = f"{method_name}_fallback_no_html"

                    sent_message = await retry_send_method(**retry_kwargs)
                    await message_dao.add_message(
                        user_id=user.id,
                        role=MessageRole.MODEL,
                        text=response_data["text"], # Save the ORIGINAL, unescaped text
                        group_id=group_db_id,
                        telegram_message_id=sent_message.message_id
                    )
                    sent_text_successfully = True # Mark as successful
                    logger.debug(f"Successfully sent message ({retry_method_name}) to chat {chat.id}. Length: {len(response_data['text'])}")

                except Exception as inner_e:
                    # Log the final failure to send text
                    logger.error(f"Failed to send message ({retry_method_name}) to {chat.id}: {inner_e}. Content (raw): '{response_data['text'][:50]}...'")
                    # Do not return yet, proceed to process commands if any

            except Exception as e:
                # Log other unexpected errors during sending
                logger.error(f"Unexpected error sending message ({method_name}, HTML) to {chat.id}: {e}")
                # Do not return yet, proceed to process commands if any

        # --- Command Processing ---
        # Process commands regardless of whether text was successfully sent,
        # unless the command itself relies on the text being sent.
        commands_to_process = response_data.get("commands", [])
        if not commands_to_process:
             logger.debug(f"No commands found in Gemini response for user {user.telegram_id} in chat {chat.id}.")

        for command_index, command in enumerate(list(commands_to_process)): # Iterate over a copy
            command_name = command.get("name")
            command_args = command.get("args", {})

            logger.info(f"Processing command {command_index+1}/{len(commands_to_process)}: '{command_name}' with args: {command_args}")

            if command_name == "do_not_respond":
                logger.info(f"Executing do_not_respond command for user {user.telegram_id}. No further action.")
                # This command should ideally prevent sending text in the first place,
                # but if text was sent, it stops further *command* processing.
                return # Stop processing this result entirely

            elif command_name == "disable_responses":
                logger.info(f"Executing disable_responses command for user {user.telegram_id}")
                success = await user_dao.update_user_settings(user_id=user.id, is_global_disabled=True)
                if success:
                    user.is_global_disabled = True # Update local user object state
                    # Send confirmation only if NO text was part of the original response
                    # or if sending the text failed.
                    if not raw_text_from_gemini or not sent_text_successfully:
                        try:
                           await message.answer("⛔️ Я більше не буду відповідати на ваші повідомлення за вашим запитом.")
                        except Exception as send_e:
                           logger.error(f"Failed to send disable_responses confirmation to {chat.id}: {send_e}")
                else:
                    logger.error(f"Failed database update for disable_responses for user {user.telegram_id}")
                    if not raw_text_from_gemini or not sent_text_successfully:
                         await send_error_message(message, "Не вдалося вимкнути відповіді. Спробуйте пізніше.")

            elif command_name == "add_reaction":
                emoji = command_args.get("emoji")
                message_ids = command_args.get("message_ids", []) # Should be a list

                if not emoji or not isinstance(emoji, str):
                    logger.warning(f"Missing or invalid emoji ('{emoji}') in add_reaction command for user {user.telegram_id}. Skipping.")
                    continue # Skip this command

                if not isinstance(message_ids, list):
                     logger.warning(f"Invalid 'message_ids' format ({type(message_ids)}) in add_reaction command. Defaulting to current message.")
                     message_ids = [] # Reset to default if format is wrong

                # If message_ids list is empty, react to the triggering user message
                if not message_ids:
                    message_ids = [message.message_id]
                    logger.info(f"No specific message_ids provided for reaction. Defaulting to triggering message ID {message.message_id} in chat {chat.id}")

                reaction_payload = [{"type": "emoji", "emoji": emoji}]

                for msg_id in message_ids:
                    if not isinstance(msg_id, int):
                         logger.warning(f"Skipping invalid message ID '{msg_id}' in add_reaction command.")
                         continue
                    try:
                        await message.bot.set_message_reaction(
                            chat_id=chat.id,
                            message_id=msg_id,
                            reaction=reaction_payload,
                            # is_big=False # Optional: for small reaction
                        )
                        logger.info(f"Added reaction '{emoji}' to message {msg_id} in chat {chat.id}")
                    except Exception as e:
                        # Common errors: message not found, chat admin rights needed, user blocked bot, reaction not allowed
                        logger.error(f"Failed to add reaction '{emoji}' to message {msg_id} in chat {chat.id}: {e}")

            elif command_name == "reply_to_message":
                # This command is primarily handled during the text sending phase.
                # Log if it appears here unexpectedly or wasn't handled.
                if not sent_text_successfully or not raw_text_from_gemini:
                     logger.warning(f"Found 'reply_to_message' command but no text was sent or sending failed. Command args: {command_args}")
                else:
                     logger.debug(f"'reply_to_message' command was handled during text sending.")

            else:
                logger.warning(f"Received unknown command '{command_name}' from Gemini. Ignoring.")

    elif result_type == "error":
        error_text = "Unknown error from Gemini API"
        if isinstance(result_data, dict):
            error_text = result_data.get("text", str(result_data))
        elif result_data:
            error_text = str(result_data)

        logger.error(f"Gemini API error for user {user.telegram_id} in chat {chat.id}: {error_text}")
        # Avoid sending duplicate error messages if one was potentially sent by the caller
        # Check if a generic error message is suitable here.
        await send_error_message(message, "Помилка під час звернення до AI. Спробуйте пізніше.")

    elif not result_type:
         logger.error(f"Received result with no 'type' from Gemini processing for user {user.telegram_id} in chat {chat.id}. Result: {gemini_result}")
         await send_error_message(message, "Отримана некоректна відповідь від AI.")

    else:
         logger.warning(f"Received unhandled result type '{result_type}' from Gemini processing for user {user.telegram_id} in chat {chat.id}. Data: {result_data}")

         
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