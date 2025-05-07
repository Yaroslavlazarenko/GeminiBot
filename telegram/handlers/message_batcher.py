"""
Message batching utility to handle rapid message sequences from users.
It batches messages of different types and triggers processing for the
last message of a sequence after a quiet period.
"""

import logging
import asyncio
import time
from typing import Dict, Optional, Tuple, Callable, Any
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
        Dependencies (bot, session_factory) are set lazily on the first message.

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
            self._session_factory: Optional[async_sessionmaker[AsyncSession]] = None

            self._initialized = True
            logger.info(f"MessageBatcher singleton initialized with wait_time={wait_time}s")

    def _set_dependencies(
        self,
        bot: Bot,
        session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Sets the dependencies if they haven't been set yet."""
        if self._bot is None:
            self._bot = bot
            self._session_factory = session_factory
            logger.info("MessageBatcher dependencies set.")

    async def handle_message(
        self,
        message: Message,
        processing_callback: ProcessingCallback,
        session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """
        Handles a new message, setting or resetting the batching timer for the user.
        """
        # Lazily set dependencies on the first call
        if self._bot is None:
            self._set_dependencies(message.bot, session_factory)

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
        """
        try:
            await asyncio.sleep(self.wait_time)
            
            current_time_after_wait = time.time()

            if user_id in self.last_message_data:
                last_ts, last_message_obj, process_cb = self.last_message_data[user_id]

                if current_time_after_wait - last_ts >= self.wait_time:
                    logger.info(f"Batching timer finished for user {user_id}. Processing message {last_message_obj.message_id}")
                    
                    # Remove the user's data BEFORE processing
                    del self.last_message_data[user_id]

                    # Create a new session for processing
                    if not self._session_factory:
                        logger.error(f"Session factory not available for user {user_id}")
                        return

                    async with self._session_factory() as session:
                        async with session.begin():
                            # Initialize DAOs with the session
                            user_dao = UserDAO(session)
                            group_dao = GroupDAO(session)
                            message_dao = MessageHistoryDAO(session)
                            
                            try:
                                await process_cb(
                                    self._bot,
                                    last_message_obj,
                                    user_dao,
                                    group_dao,
                                    message_dao
                                )
                                logger.debug(f"Processing callback finished for user {user_id}, message {last_message_obj.message_id}")
                            except asyncio.CancelledError:
                                logger.warning(f"Processing callback for user {user_id} was cancelled")
                                raise
                            except Exception as e:
                                logger.error(f"Error in processing callback for user {user_id}: {e}", exc_info=True)
                                if self._bot:
                                    try:
                                        await self._bot.send_message(
                                            chat_id=last_message_obj.chat.id,
                                            text="🤯 Ой! Сталася помилка під час обробки повідомлення."
                                        )
                                    except Exception as send_e:
                                        logger.error(f"Failed to send error message to user {user_id}: {send_e}")
                else:
                    logger.debug(f"Timer for user {user_id} finished but new message arrived. Skipping processing.")

        except asyncio.CancelledError:
            logger.debug(f"Timer task for user {user_id} was cancelled")
            raise
            
        except Exception as e:
            logger.error(f"Unexpected error in batching timer for user {user_id}: {e}", exc_info=True)

        finally:
            if user_id in self.active_timers and self.active_timers[user_id] == asyncio.current_task():
                del self.active_timers[user_id]
                logger.debug(f"Removed user {user_id} from active timers")

# Create a global instance of the batcher
message_batcher = MessageBatcher()