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

    async def get_or_create_user(self, telegram_id: int, username: str | None = None, first_name: str | None = None, last_name: str | None = None, **kwargs) -> User:
        values_to_insert = {
            "telegram_id": telegram_id,
            "username": username if username is not None else str(telegram_id),
            "first_name": first_name,
            "last_name": last_name,
            **kwargs
        }
        values_to_update = {
            "username": username if username is not None else str(telegram_id),
            "first_name": first_name,
            "last_name": last_name,
        }
        insert_stmt = pg_insert(User).values(**values_to_insert).on_conflict_do_update(
            index_elements=['telegram_id'], set_=values_to_update
        ).returning(User)
        try:
            result = await self.session.execute(insert_stmt)
            return result.scalar_one()
        except SQLAlchemyError as e:
            logger.error(f"Database error during get_or_create_user for telegram_id={telegram_id}: {e}", exc_info=True)
            raise

    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        logger.debug(f"Getting user by telegram_id={telegram_id}")
        try:
            stmt = select(User).where(User.telegram_id == telegram_id)
            result = await self.session.execute(stmt)
            user = result.scalar_one_or_none()
            if user: logger.debug(f"User found for telegram_id={telegram_id}, user_id={user.id}")
            else: logger.debug(f"User not found for telegram_id={telegram_id}")
            return user
        except SQLAlchemyError as e:
            logger.error(f"Error getting user by telegram_id={telegram_id}: {e}", exc_info=True)
            raise

    async def update_user_settings(self, user_id: int, responds_to_text: bool | None = None, responds_to_voice: bool | None = None, responds_to_photo: bool | None = None, transcribe_voice_only: bool | None = None) -> bool:
        logger.debug(f"Updating settings for user_id={user_id}")
        values_to_update = {}
        if responds_to_text is not None: values_to_update["responds_to_text"] = responds_to_text
        if responds_to_voice is not None: values_to_update["responds_to_voice"] = responds_to_voice
        if responds_to_photo is not None: values_to_update["responds_to_photo"] = responds_to_photo
        if transcribe_voice_only is not None: values_to_update["transcribe_voice_only"] = transcribe_voice_only
        if not values_to_update:
            logger.warning(f"No settings provided to update for user_id={user_id}")
            return False
        stmt = update(User).where(User.id == user_id).values(**values_to_update)
        try:
            result = await self.session.execute(stmt)
            if result.rowcount > 0:
                logger.info(f"Successfully updated settings for user_id={user_id}")
                return True
            else:
                logger.warning(f"User with id={user_id} not found for settings update or settings unchanged.")
                user_exists = await self.session.get(User, user_id)
                return user_exists is not None
        except SQLAlchemyError as e:
            logger.error(f"Database error updating settings for user_id={user_id}: {e}", exc_info=True)
            raise # Re-raise the exception