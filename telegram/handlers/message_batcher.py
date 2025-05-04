"""
Message batching utility to handle rapid message sequences from users.
It batches messages of different types and triggers processing for the
last message of a sequence after a quiet period.
"""

import logging
import asyncio
import time
from typing import Dict, Set, Optional, Tuple, Callable, Any

# Assume these are available and correctly typed from your project
# from database.dao import UserDAO, GroupDAO, MessageHistoryDAO
# from aiogram import Bot
# from aiogram.types import Message

# Placeholder types if actual types are not in this file context
UserDAO = Any
GroupDAO = Any
MessageHistoryDAO = Any
Bot = Any
Message = Any # In real code, import aiogram.types.Message

logger = logging.getLogger(__name__)

# Define the expected signature for processing callbacks
# These callbacks will be responsible for fetching history, calling AI, sending response
ProcessingCallback = Callable[[Bot, Message, UserDAO, GroupDAO, MessageHistoryDAO], Any]

class MessageBatcher:
    """
    Manages batching of messages from users across different handler types.
    Triggers the specific processing logic for the last message in a rapid sequence.
    """

    _instance: Optional['MessageBatcher'] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(MessageBatcher, cls).__new__(cls)
        return cls._instance

    def __init__(self, wait_time: float = 1.5):
        """
        Initialize the message batcher (singleton).
        Dependencies (bot, daos) are set lazily on the first message.

        Args:
            wait_time: Time in seconds to wait for additional messages before processing.
        """
        if not hasattr(self, '_initialized'):
            self.wait_time: float = wait_time
            # Stores user_id -> (timestamp_of_last_message, last_message_object, processing_callback)
            self.last_message_data: Dict[int, Tuple[float, Message, ProcessingCallback]] = {}
            # Maps user_id to the active timer task
            self.active_timers: Dict[int, asyncio.Task] = {}

            # Dependencies - will be set lazily
            self._bot: Optional[Bot] = None
            self._user_dao: Optional[UserDAO] = None
            self._group_dao: Optional[GroupDAO] = None
            self._message_dao: Optional[MessageHistoryDAO] = None

            self._initialized = True
            logger.info(f"MessageBatcher singleton initialized with wait_time={wait_time}s")
        else:
             # Singleton already initialized, update wait_time if provided (optional behavior)
             if 'wait_time' in kwargs:
                 self.wait_time = kwargs['wait_time']
                 logger.debug(f"MessageBatcher wait_time updated to {self.wait_time}s")


    def _set_dependencies(
        self,
        bot: Bot,
        user_dao: UserDAO,
        group_dao: GroupDAO,
        message_dao: MessageHistoryDAO
    ) -> None:
        """Sets the dependencies if they haven't been set yet."""
        if self._bot is None:
            self._bot = bot
            self._user_dao = user_dao
            self._group_dao = group_dao
            self._message_dao = message_dao
            logger.info("MessageBatcher dependencies set.")

    async def handle_message(
        self,
        message: Message,
        processing_callback: ProcessingCallback,
        user_dao: UserDAO,
        group_dao: GroupDAO,
        message_dao: MessageHistoryDAO
    ) -> None:
        """
        Handles a new message, setting or resetting the batching timer for the user.
        Stores the message and the specific processing callback for later execution.

        Args:
            message: The incoming Aiogram Message object.
            processing_callback: The async callable specific to this message type's
                                 processing logic. It must accept (Bot, Message, UserDAO, GroupDAO, MessageHistoryDAO).
            user_dao: User DAO from handler dependencies.
            group_dao: Group DAO from handler dependencies.
            message_dao: MessageHistory DAO from handler dependencies.
        """
        # Lazily set dependencies on the first call
        if self._bot is None:
            self._set_dependencies(message.bot, user_dao, group_dao, message_dao)
        # Ensure passed dependencies match the stored ones if they were already set,
        # or update them? Let's assume consistency for simplicity.
        # If you have multiple dispatchers/bots, this singleton approach needs refinement.
        # For a single bot/set of DAOs, this lazy init works.

        user_id = message.from_user.id
        chat_id = message.chat.id
        current_time = time.time()

        logger.debug(f"Batcher received message {message.message_id} from user {user_id} in chat {chat_id}")

        # Store the latest message object and its specific processing callback
        self.last_message_data[user_id] = (current_time, message, processing_callback)

        # Cancel any existing timer for this user
        if user_id in self.active_timers:
            old_task = self.active_timers[user_id]
            if not old_task.done():
                old_task.cancel()
                logger.debug(f"Cancelled existing timer for user {user_id}")
            del self.active_timers[user_id]

        # Start a new timer task
        task = asyncio.create_task(self._process_after_timeout(user_id))
        self.active_timers[user_id] = task
        logger.debug(f"Started new timer task for user {user_id}")

    async def _process_after_timeout(self, user_id: int) -> None:
        """
        Waits for the specified time and then triggers processing
        if no new messages were received during the wait period.

        Args:
            user_id: The Telegram user ID.
        """
        try:
            # Wait for the batching period to potentially receive more messages
            await asyncio.sleep(self.wait_time)

            # After waiting, check if the message data we stored when this timer started
            # is still the LATEST data for this user and if the quiet time is met.
            current_time_after_wait = time.time()

            # Check if the user data still exists and if enough time has passed
            # compared to the timestamp stored with the data
            if user_id in self.last_message_data:
                 last_ts, last_message_obj, process_cb = self.last_message_data[user_id]

                 if current_time_after_wait - last_ts >= self.wait_time:
                    # This condition means no new message from this user arrived
                    # for the last `self.wait_time` seconds *leading up to this moment*.
                    # This is the trigger to process the last message in the batch.

                    logger.info(f"Batching timer finished for user {user_id}. Triggering processing for last message (ID: {last_message_obj.message_id}) in chat {last_message_obj.chat.id} (time since last: {current_time_after_wait - last_ts:.2f}s)")

                    # Remove the user's data BEFORE processing
                    del self.last_message_data[user_id]

                    # --- Trigger the actual message processing logic ---
                    # Ensure dependencies are available (should be set by handle_message)
                    if not all([self._bot, self._user_dao, self._group_dao, self._message_dao]):
                        logger.error(f"Batcher dependencies not set for user {user_id}. Cannot process message.")
                        # Attempt to send an error message to the user
                        if self._bot:
                            try:
                                await self._bot.send_message(
                                     chat_id=last_message_obj.chat.id,
                                     text="🤯 Ой! Внутрішня помилка батчера: залежності не встановлені. Спробуйте пізніше."
                                )
                            except Exception as send_e:
                                logger.error(f"Failed to send dependency error message to user {user_id}: {send_e}")
                        return

                    try:
                        # Call the specific processing callback with stored dependencies
                        await process_cb(
                            self._bot,
                            last_message_obj,
                            self._user_dao,
                            self._group_dao,
                            self._message_dao
                        )
                        logger.debug(f"Processing callback finished for user {user_id}, message {last_message_obj.message_id}")
                    except asyncio.CancelledError:
                        logger.warning(f"Processing callback for user {user_id}, message {last_message_obj.message_id} was cancelled.")
                        # If cancelled, maybe it was a graceful shutdown? No need to re-add data.
                        pass
                    except Exception as e:
                        logger.error(f"Error in processing callback for user {user_id}, message {last_message_obj.message_id}: {e}", exc_info=True)
                        # Send error message using the stored bot instance
                        if self._bot:
                           try:
                                await self._bot.send_message(
                                    chat_id=last_message_obj.chat.id,
                                    text="🤯 Ой! Сталася неочікувана помилка під час обробки вашого запиту після батчинга."
                                )
                           except Exception as send_e:
                                logger.error(f"Failed to send error message after processing callback failure for user {user_id}: {send_e}")

                 else:
                    # No, a new message arrived and updated the timestamp before this timer finished.
                    # This timer's purpose is fulfilled by a newer timer. Do nothing.
                    logger.debug(f"Timer for user {user_id} finished, but new message arrived ({current_time_after_wait - last_ts:.2f}s since last < {self.wait_time}s). Not triggering processing.")

            else:
                # User data was already processed and removed by another timer task
                # (e.g., a faster timer instance from a later message) or explicitly cleared.
                logger.debug(f"Timer for user {user_id} finished, but data already processed or removed.")

        except asyncio.CancelledError:
             # This timer task was cancelled by a new message arriving via handle_message.
             logger.debug(f"Batching timer task for user {user_id} was cancelled.")
             pass # No action needed, the new message started a new timer

        except Exception as e:
            logger.error(f"Unexpected error in batching timer for user {user_id}: {e}", exc_info=True)

        finally:
            # Ensure the timer task is removed from the active list
            # Check needed because it might have been removed by a faster timer
            if user_id in self.active_timers and self.active_timers[user_id] == asyncio.current_task():
                 del self.active_timers[user_id]
                 logger.debug(f"Removed user {user_id} from active_timers list")

# Create a global instance of the batcher
# You can pass the default wait_time here, or it will use the class default (1.5s)
# message_batcher = MessageBatcher(wait_time=1.5)
# Or just use the default:
message_batcher = MessageBatcher()