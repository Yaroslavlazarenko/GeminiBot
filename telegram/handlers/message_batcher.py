"""
Message batching utility to handle rapid message sequences from users.
This helps prevent the bot from responding to each message when a user
is forwarding multiple messages in quick succession.
"""

import logging
import asyncio
from typing import Dict, Set, Optional
import time

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
        self.last_message_time: Dict[int, float] = {}  # Maps user_id to timestamp of last message
        self.processing_lock: Set[int] = set()  # Set of users currently being processed
    
    async def should_process_message(self, user_id: int) -> bool:
        """
        Determine if we should process a message from this user now.
        
        Args:
            user_id: The Telegram user ID.
            
        Returns:
            True if the message should be processed, False if it should be ignored.
        """
        current_time = time.time()
        
        # Если это первое сообщение от пользователя
        if user_id not in self.last_message_time:
            self.last_message_time[user_id] = current_time
            logger.debug(f"First message from user {user_id}, processing immediately")
            return True
        
        # Проверяем, прошло ли достаточно времени с момента последнего сообщения
        time_since_last = current_time - self.last_message_time[user_id]
        
        # Если прошло достаточно времени, обрабатываем сообщение
        if time_since_last >= self.wait_time:
            # Только теперь обновляем время последнего сообщения
            self.last_message_time[user_id] = current_time
            logger.debug(f"Sufficient time ({time_since_last:.2f}s) since last message from user {user_id}, processing")
            return True
        
        # Если сообщение пришло слишком быстро после предыдущего, игнорируем его
        logger.info(f"Message from user {user_id} came too soon ({time_since_last:.2f}s < {self.wait_time}s), ignoring")
        
        # Обновляем время последнего сообщения, чтобы отсчитывать время от него
        self.last_message_time[user_id] = current_time
        
        # Запускаем таймер для обработки последнего сообщения через wait_time секунд
        # Отменяем предыдущий таймер и создаем новый
        if user_id in self.processing_lock:
            # Уже есть таймер, но пришло новое сообщение, поэтому мы не удаляем из блокировки
            logger.debug(f"User {user_id} already has a timer, resetting it")
        else:
            # Нет активного таймера, создаем новый
            self.processing_lock.add(user_id)
            logger.debug(f"Starting timer for user {user_id}")
            
        # В любом случае создаем новый таймер
        asyncio.create_task(self._process_after_timeout(user_id))
        
        return False
    
    async def _process_after_timeout(self, user_id: int) -> None:
        """
        Wait for the specified time and then allow processing the last message.
        
        Args:
            user_id: The Telegram user ID.
        """
        try:
            # Ждем указанное время
            await asyncio.sleep(self.wait_time)
            
            # Получаем текущее время и время последнего сообщения
            current_time = time.time()
            last_message_time = self.last_message_time.get(user_id, 0)
            time_since_last = current_time - last_message_time
            
            # Если с момента последнего сообщения прошло достаточно времени,
            # значит новых сообщений не было и можно обработать последнее
            if time_since_last >= self.wait_time:
                logger.info(f"Processing last message from user {user_id} after batching period (time since last: {time_since_last:.2f}s)")
                # Здесь мы не делаем ничего, так как обработка будет выполнена
                # при следующем вызове should_process_message
            else:
                # Если за время ожидания пришли новые сообщения, запускаем новый таймер
                logger.debug(f"New messages received during wait time for user {user_id}, not processing yet")
                # Не создаем новый таймер здесь, он будет создан в should_process_message
                # при следующем сообщении
            
        except Exception as e:
            logger.error(f"Error in batching timer for user {user_id}: {e}", exc_info=True)
        finally:
            # В любом случае удаляем пользователя из списка блокировки
            if user_id in self.processing_lock:
                self.processing_lock.remove(user_id)
                logger.debug(f"Removed user {user_id} from processing lock")

# Global instance to be used across all message handlers
message_batcher = MessageBatcher()
