from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from database.manager import DatabaseManager
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
        
        data["db_manager"] = self.db_manager
        
        user = None
        group = None
        
        # Determine the user object based on event type
        from_user = event.from_user if isinstance(event, Message) else event.from_user if isinstance(event, CallbackQuery) else None
        
        if from_user:
            user = await self.db_manager.get_or_create_user(
                telegram_id=from_user.id,
                username=from_user.username,
                first_name=from_user.first_name,
                last_name=from_user.last_name
            )
            data["user"] = user

        # Check if the event happened in a group
        chat = event.chat if isinstance(event, Message) else event.message.chat if isinstance(event, CallbackQuery) else None
        
        if chat and chat.type in ['group', 'supergroup']:
            group = await self.db_manager.get_or_create_group(
                telegram_chat_id=chat.id,
                name=chat.title or "Unknown Group"
            )
            data["group"] = group
            
            # If in group, use group history. If in private, use user history.
            data["history_context"] = group.get("history", [])
            data["context_id"] = chat.id
            data["is_group"] = True
        elif chat and chat.type == 'private' and user:
            data["history_context"] = user.get("history", [])
            data["context_id"] = from_user.id
            data["is_group"] = False

        return await handler(event, data)
