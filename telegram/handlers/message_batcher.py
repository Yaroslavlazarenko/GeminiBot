"""
Message batching utility to handle rapid message sequences from users.
This helps prevent the bot from responding to each message when a user
is forwarding multiple messages in quick succession.
"""

import logging
import asyncio
from typing import Dict, Set, Optional, Callable, Any
import time
from datetime import datetime

logger = logging.getLogger(__name__)

class MessageBatcher:
    """
    Manages batching of messages from users to prevent responding to each message
    when a user sends multiple messages in quick succession.
    """
    
    def __init__(self, wait_time: float = 1.5):
        """
        Initialize the message batcher.
        
        Args:
            wait_time: Time in seconds to wait for additional messages before processing.
        """
        self.wait_time = wait_time
        self.user_timers: Dict[int, asyncio.Task] = {}  # Maps user_id to timer tasks
        self.active_users: Set[int] = set()  # Set of users with active batching
        self.last_message_time: Dict[int, float] = {}  # Maps user_id to timestamp of last message
        self.last_message_ready: int = None  # ID of user whose last message is ready to be processed
    
    def is_user_active(self, user_id: int) -> bool:
        """Check if a user has an active batching session."""
        return user_id in self.active_users
    
    def get_time_since_last_message(self, user_id: int) -> Optional[float]:
        """Get time in seconds since the user's last message."""
        if user_id in self.last_message_time:
            return time.time() - self.last_message_time[user_id]
        return None
    
    async def register_message(self, user_id: int) -> bool:
        """
        Register a new message from a user and determine if it should be processed.
        
        Args:
            user_id: The Telegram user ID.
            
        Returns:
            True if the message should be processed immediately, False if it's being batched.
        """
        current_time = time.time()
        self.last_message_time[user_id] = current_time
        
        # Если это первое сообщение после долгого перерыва, обработаем его сразу
        if user_id not in self.active_users and user_id not in self.user_timers:
            # Но также запустим таймер для следующих сообщений
            self.active_users.add(user_id)
            self.user_timers[user_id] = asyncio.create_task(
                self._wait_and_release(user_id)
            )
            logger.debug(f"Started batching for user {user_id}, but processing first message immediately")
            return True  # Обработать первое сообщение сразу
        
        # If user already has an active batching session
        if user_id in self.active_users:
            # Cancel and replace the existing timer
            if user_id in self.user_timers and not self.user_timers[user_id].done():
                self.user_timers[user_id].cancel()
            
            # Create a new timer
            self.user_timers[user_id] = asyncio.create_task(
                self._wait_and_release(user_id)
            )
            logger.debug(f"User {user_id} sent another message during batching, timer reset")
            return False  # Don't process this message immediately
        
        # Этот код не должен выполняться, но на всякий случай
        logger.warning(f"Unexpected state for user {user_id} in register_message")
        self.active_users.add(user_id)
        self.user_timers[user_id] = asyncio.create_task(
            self._wait_and_release(user_id)
        )
        return False  # Don't process this message immediately
    
    async def _wait_and_release(self, user_id: int) -> None:
        """
        Wait for the specified time and then release the user from batching.
        Also process the last message received during the batching period.
        
        Args:
            user_id: The Telegram user ID.
        """
        try:
            await asyncio.sleep(self.wait_time)
            if user_id in self.active_users:
                self.active_users.remove(user_id)
                logger.debug(f"Released user {user_id} from batching after {self.wait_time}s of inactivity")
                
                # Signal that the last message can now be processed
                # This is done by setting a flag that handlers can check
                self.last_message_ready = user_id
                logger.info(f"Marked last message from user {user_id} as ready for processing")
                
                # Schedule automatic cleanup of the ready flag after a short time
                asyncio.create_task(self._cleanup_ready_flag(user_id))
        except asyncio.CancelledError:
            # Task was cancelled because user sent another message
            logger.debug(f"Timer for user {user_id} was cancelled because they sent another message")
            pass
        except Exception as e:
            logger.error(f"Error in batching timer for user {user_id}: {e}", exc_info=True)
            # Make sure to clean up even if there's an error
            if user_id in self.active_users:
                self.active_users.remove(user_id)
    
    async def _cleanup_ready_flag(self, user_id: int) -> None:
        """
        Clean up the ready flag after a short delay to prevent race conditions.
        """
        await asyncio.sleep(0.5)  # Short delay
        if hasattr(self, 'last_message_ready') and self.last_message_ready == user_id:
            self.last_message_ready = None
            
    async def should_process_now(self, user_id: int) -> bool:
        """
        Check if we should process a message for this user now.
        This is called after the batching period has ended.
        
        Args:
            user_id: The Telegram user ID.
            
        Returns:
            True if the message should be processed now, False otherwise.
        """
        # Если пользователь не в режиме батчинга и нет активного таймера,
        # это означает, что сообщение нужно обработать сразу
        if user_id not in self.active_users and user_id not in self.user_timers:
            logger.debug(f"User {user_id} is not in batching mode, processing message immediately")
            return True
            
        # Если таймер сработал и сообщение помечено как готовое к обработке
        if hasattr(self, 'last_message_ready') and self.last_message_ready == user_id:
            logger.debug(f"Last message from user {user_id} is ready for processing after batching")
            return True
            
        # В противном случае, сообщение еще в режиме батчинга
        return False

# Global instance to be used across all message handlers
message_batcher = MessageBatcher()
