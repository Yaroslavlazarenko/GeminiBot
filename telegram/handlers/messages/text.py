# ... existing imports ...
import logging

from aiogram import F, Router, Bot
from aiogram.types import Message
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramForbiddenError

from ai.gemini_client import get_text_response
from database.models import User, MessageRole
from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
from ..utils import send_error_message, get_group_or_none, handle_gemini_result
# Import the global batcher instance
from ..message_batcher import message_batcher, ProcessingCallback # Import the batcher instance and the callback type hint

# Define the logger for this module
logger = logging.getLogger(__name__)

# Create the router
router = Router()

# --- Actual Processing Logic for Text Messages ---
# This function is called by the MessageBatcher when the quiet period is met.
# It contains the core logic for handling a text message.
async def actual_text_processing_logic(
    bot: Bot,
    message: Message,
    user_dao: UserDAO,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO,
) -> None:
    """
    Performs the actual processing logic for a text message after batching.
    This function is called by the MessageBatcher. It fetches history,
    calls the AI, saves the response, and sends it to the user.
    It assumes the incoming message has already been saved to the DB.
    """
    chat = message.chat
    user_telegram_id = message.from_user.id
    chat_id = chat.id

    logger.info(f"Starting batched text processing for user {user_telegram_id} in chat {chat_id} (last message ID: {message.message_id})")

    try:
        # Re-fetch User object to ensure we have the latest settings
        # Note: Assumes user_dao.get_user_by_telegram_id method exists and works
        user = await user_dao.get_user_by_telegram_id(user_telegram_id)
        if not user:
             logger.error(f"User {user_telegram_id} not found in DB during batched processing. Cannot proceed.")
             # Attempt to send an error message via the bot instance passed here
             try:
                  await bot.send_message(chat_id=chat_id, text="🤯 Не можу знайти ваші дані для обробки повідомлення. Спробуйте написати знову.")
             except Exception as send_e:
                  logger.error(f"Failed to send user data error message to {chat_id}: {send_e}")
             return # Stop processing

        # Get group context (will be None for private chats)
        group = await get_group_or_none(group_dao, chat) # Use your utility function
        group_db_id = group.id if group else None

        # Check global/text response settings again (could have changed since message arrived)
        if user.is_global_disabled or not getattr(user, 'responds_to_text', True):
            logger.debug(f"Ignoring batched processing for user {user_telegram_id} due to updated user settings.")
            return # User settings now disable response

        if group and (group.is_global_disabled or not getattr(group, 'responds_to_text', True)):
             logger.debug(f"Ignoring batched processing for user {user_telegram_id} in group {chat_id} due to updated group settings.")
             return # Group settings now disable response


        # Retrieve the full message history for the chat
        # This will include the current message and any others received during the batching period
        if group_db_id is not None:
            message_history = await message_dao.get_group_messages_as_contents(group_id=group_db_id)
            logger.debug(f"Retrieved {len(message_history)} messages for group chat history (group_db_id: {group_db_id})")
        else:
            message_history = await message_dao.get_user_private_messages_as_contents(user_id=user.id) # Use internal user ID
            logger.debug(f"Retrieved {len(message_history)} messages for private chat history (user_id: {user.id})")

        if not message_history:
            logger.warning(f"Message history is unexpectedly empty for user {user_telegram_id} / chat {chat_id} after batching.")
            return # Nothing to process

        # Send typing indicator *before* calling Gemini
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception as e:
            logger.warning(f"Failed to send chat action 'typing' to {chat_id} during batched processing: {e}")

        # Call Gemini with the history
        gemini_result = await get_text_response(
            message_history=message_history,
            user=user, # Pass the re-fetched user object
            message=message # Pass the last message object from the batch
        )

        # Handle Gemini response (saving to DB, sending message to user)
        # This utility should use the 'bot' instance passed to this function
        await handle_gemini_result(
            gemini_result,
            message, # Pass the last message object
            message_dao=message_dao, # Pass DAOs
            user_dao=user_dao,
            user=user, # Pass the re-fetched user object
            group_db_id=group_db_id # Pass group ID
        )

        logger.info(f"Successfully processed batched text message for user {user_telegram_id} in chat {chat_id}")

    except Exception as e:
        # Catch exceptions during the actual processing logic
        logger.error(f"Error in batched text processing logic for user {user_telegram_id} in chat {chat_id} (last message ID: {message.message_id}): {e}", exc_info=True)
        # Use the bot instance passed to this function to send an error message
        try:
            await send_error_message(message, "🤯 Ой! Сталася неочікувана помилка під час обробки вашого текстового повідомлення після батчинга.")
        except Exception as send_e:
             logger.error(f"Failed to send error message after batched text processing failure for user {user_telegram_id}: {send_e}")


# --- Handler that uses the Batcher ---
@router.message(F.text)
async def text_handler(
    message: Message,
    group_dao: GroupDAO,
    message_dao: MessageHistoryDAO,
    user_dao: UserDAO,
    user: User # Assuming user is provided by middleware and is the DB User object
) -> None:
    """
    Handles incoming text messages. Saves the message to DB and
    passes it to the message batcher for timed processing.
    """
    chat = message.chat
    user_display_name = message.from_user.full_name or f"User {user.telegram_id}"
    chat_id = chat.id

    logger.debug(f"Received text message {message.message_id} from user {user_display_name} (ID: {user.telegram_id}) in chat {chat_id}. Saving to DB.")

    # --- Immediate actions (always done) ---

    # Formulate metadata
    is_forwarded = bool(message.forward_from or message.forward_from_chat or message.forward_sender_name or message.forward_date)
    metadata = f"Message info: text message from {user_display_name} (User ID: {user.telegram_id})"
    if is_forwarded:
        metadata = f"Message info: FORWARDED text message shared by {user_display_name} (User ID: {user.telegram_id})"
        # Add detailed forwarding info as before
        if message.forward_from:
            forward_name = message.forward_from.full_name or message.forward_from.username or f"User {message.forward_from.id}"
            is_bot = "(Bot)" if message.forward_from.is_bot else ""
            metadata += f"\nOriginal sender: {forward_name} {is_bot} (ID: {message.forward_from.id})"
        elif message.forward_sender_name:
             metadata += f"\nOriginal sender: {message.forward_sender_name} (forwarding privacy enabled)"
        elif message.forward_from_chat:
            chat_type = message.forward_from_chat.type.capitalize()
            metadata += f"\nOriginal source: {chat_type} '{message.forward_from_chat.title}' (ID: {message.forward_from_chat.id})"
            if message.forward_signature:
                metadata += f"\nPost author: {message.forward_signature}"
        if message.forward_date:
             metadata += f"\nOriginal message time: {message.forward_date}"

    metadata += f", Message ID: {message.message_id}, Current time: {message.date}"

    # Get group context for saving to DB
    group = await get_group_or_none(group_dao, chat) if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP] else None
    group_db_id = group.id if group else None

    # Add message to history immediately
    try:
        await message_dao.add_message(
            user_id=user.id, # Use internal DB user ID from middleware
            role=MessageRole.USER,
            text=message.text,
            group_id=group_db_id,
            telegram_message_id=message.message_id,
            message_metadata=metadata
        )
        logger.debug(f"User text message {message.message_id} saved to DB (user {user.telegram_id}, group_id {group_db_id})")
    except Exception as e:
         logger.error(f"Failed to save user text message {message.message_id} to DB: {e}", exc_info=True)
         # If saving fails, we cannot reliably process this message later.
         # Send an error and stop.
         await send_error_message(message, "Не вдалося зберегти ваше текстове повідомлення.")
         return # Cannot proceed if message isn't saved

    # --- Pass to Batcher ---
    # Pass the message and the specific processing function for text messages,
    # along with the DAOs the batcher might need to set for lazy initialization.
    try:
        await message_batcher.handle_message(
            message=message,
            processing_callback=actual_text_processing_logic, # Pass the reference to the text processing function
            user_dao=user_dao, # Pass dependencies
            group_dao=group_dao,
            message_dao=message_dao
        )
        logger.debug(f"Text message {message.message_id} from user {user.telegram_id} passed to batcher.")
    except Exception as e:
        # If batcher fails (e.g., internal error, couldn't start timer), processing won't happen.
        logger.error(f"Error passing text message {message.message_id} to batcher for user {user.telegram_id}: {e}", exc_info=True)
        await send_error_message(message, "Виникла проблема з системою обробки текстових повідомлень. Спробуйте знову.")

    # The handler's job is done. The batcher will trigger the actual_text_processing_logic later.
    # Do NOT add the response logic here.