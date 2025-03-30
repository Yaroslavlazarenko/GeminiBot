from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from services.database.dao import DAO
from services.database.manager import DatabaseManager
from services.database.models import User  # Import the User model


class DAOMiddleware(BaseMiddleware):
    def __init__(self, database_manager: DatabaseManager) -> None:
        self.database_manager = database_manager

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        session_maker = self.database_manager.session()
        async with session_maker() as session:  # Create session using session_maker
            dao = DAO(session)  # Pass the session to DAO
            data["dao"] = dao  # Make DAO available to handlers
            user = event.from_user
            if user:
                existing_user = await dao.get_user_by_telegram_id(user.id)
                if not existing_user:
                    new_user = await dao.create_user(
                        username=user.username or str(user.id),  # fallback to user id if username is None, make sure its string
                        telegram_id=user.id,
                        first_name=user.first_name,
                        last_name=user.last_name,
                    )
                    data["user"] = new_user
                else:
                    data["user"] = existing_user
            else:
                data["user"] = None  # set to none when no user object
            try:
                result = await handler(event, data)
            except Exception as e:
                print(f"Handler error: {e}")
                raise e
            finally:
                await session.close()  # Close the session after use

            return result