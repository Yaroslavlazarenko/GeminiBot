from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from database.manager import DatabaseManager, ChatContext
from typing import Any, Awaitable, Callable, Dict

class DatabaseMiddleware(BaseMiddleware):
    def __init__(self, db_manager: DatabaseManager):
        super().__init__()
        self.db_manager = db_manager

    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        
        chat_context = None
        
        # Check if the event happened in a group
        chat = event.chat if isinstance(event, Message) else event.message.chat if isinstance(event, CallbackQuery) else None
        from_user = event.from_user if isinstance(event, Message) else event.from_user if isinstance(event, CallbackQuery) else None

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
