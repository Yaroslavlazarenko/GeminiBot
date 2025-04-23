# services/database/user_dao.py
import logging
from typing import Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..models import User

logger = logging.getLogger(__name__)

class UserDAO:
    """Асинхронный DAO для работы с моделью User."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_by_internal_id(self, user_id: int) -> Optional[User]:
        try:
            stmt = select(User).where(User.id == user_id)
            result = await self.session.execute(stmt)
            return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            logger.critical(f"Error getting user by internal id={user_id}: {e}", exc_info=True)
            raise

    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        try:
            stmt = select(User).where(User.telegram_id == telegram_id)
            result = await self.session.execute(stmt)
            return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            logger.critical(f"Error getting user by telegram_id={telegram_id}: {e}", exc_info=True)
            raise

    async def get_or_create_user(self, telegram_id: int, username: str | None = None, first_name: str | None = None, last_name: str | None = None) -> User:
        values_to_insert = {
            "telegram_id": telegram_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name
        }
        values_to_update = {
            "username": username,
            "first_name": first_name,
            "last_name": last_name
        }

        insert_stmt = pg_insert(User).values(**values_to_insert).on_conflict_do_update(
            index_elements=['telegram_id'],
            set_=values_to_update
        ).returning(User)

        try:
            result = await self.session.execute(insert_stmt)
            return result.scalar_one()
        except SQLAlchemyError as e:
            logger.critical(f"Database error during get_or_create_user for telegram_id={telegram_id}: {e}", exc_info=True)
            raise

    async def update_user_settings(
        self,
        user_id: int,
        responds_to_text: bool | None = None,
        responds_to_voice: bool | None = None,
        responds_to_photo: bool | None = None,
        responds_to_video_note: bool | None = None,
        transcribe_voice_only: bool | None = None,
        is_admin: bool | None = None
    ) -> bool:
        """Update user settings."""
        try:
            async with self.session() as session:
                user = await session.get(User, user_id)
                if not user:
                    return False

                if responds_to_text is not None:
                    user.responds_to_text = responds_to_text
                if responds_to_voice is not None:
                    user.responds_to_voice = responds_to_voice
                if responds_to_photo is not None:
                    user.responds_to_photo = responds_to_photo
                if responds_to_video_note is not None:
                    user.responds_to_video_note = responds_to_video_note
                if transcribe_voice_only is not None:
                    user.transcribe_voice_only = transcribe_voice_only
                if is_admin is not None:
                    user.is_admin = is_admin

                await session.commit()
                return True
        except Exception as e:
            logger.error(f"Error updating user settings: {e}")
            return False