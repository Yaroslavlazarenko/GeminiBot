import time
import logging
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from core.database import DatabaseManager, ChatContext
from typing import Any, Awaitable, Callable, Dict

logger = logging.getLogger(__name__)

class DeduplicationMiddleware(BaseMiddleware):
    """Prevents processing duplicate Telegram updates.
    
    Telegram may re-deliver updates if the bot takes too long to respond,
    or if multiple polling instances briefly overlap. This middleware
    tracks recently seen (chat_id, message_id) pairs and silently drops
    duplicates within a configurable TTL window.
    """
    
    def __init__(self, ttl_seconds: int = 120):
        super().__init__()
        self._seen: Dict[tuple, float] = {}
        self._ttl = ttl_seconds
        self._last_cleanup = time.monotonic()
        self._cleanup_interval = 60  # Cleanup stale entries every 60 seconds

    def _cleanup(self):
        """Remove expired entries to prevent unbounded memory growth."""
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        expired = [k for k, v in self._seen.items() if now - v > self._ttl]
        for k in expired:
            del self._seen[k]

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any]
    ) -> Any:
        self._cleanup()
        
        # Build a unique key for this event
        chat = getattr(event, "chat", None)
        message_id = getattr(event, "message_id", None)
        
        if chat and message_id:
            key = (chat.id, message_id)
            now = time.monotonic()
            
            if key in self._seen:
                logger.warning(
                    f"Duplicate update dropped: chat_id={chat.id}, message_id={message_id}"
                )
                return  # Silently drop the duplicate
            
            self._seen[key] = now

        return await handler(event, data)


class DatabaseMiddleware(BaseMiddleware):
    def __init__(self, db_manager: DatabaseManager):
        super().__init__()
        self.db_manager = db_manager

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any]
    ) -> Any:
        
        chat_context = None
        
        # Resolve chat and from_user in a generic way
        chat = getattr(event, "chat", None)
        if not chat and hasattr(event, "message") and event.message:
            chat = getattr(event.message, "chat", None)
            
        from_user = getattr(event, "from_user", None) or getattr(event, "user", None)

        if chat and chat.type in ['group', 'supergroup']:
            doc = await self.db_manager.get_or_create_group(
                telegram_chat_id=chat.id,
                name=chat.title or "Unknown Group"
            )
            chat_context = ChatContext(self.db_manager, chat.id, True, doc)
            
        elif chat and chat.type == 'private' and from_user:
            doc = await self.db_manager.get_or_create_user(
                telegram_id=from_user.id,
                username=from_user.username,
                first_name=from_user.first_name,
                last_name=from_user.last_name
            )
            chat_context = ChatContext(self.db_manager, from_user.id, False, doc)

        # Inject the unified context into data
        if chat_context:
            data["chat_context"] = chat_context

        return await handler(event, data)
