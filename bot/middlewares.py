from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from core.database import DatabaseManager, ChatContext
from typing import Any, Awaitable, Callable, Dict

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
