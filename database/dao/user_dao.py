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
        is_global_disabled: bool | None = None,
        responds_to_text: bool | None = None,
        responds_to_voice: bool | None = None,
        responds_to_photo: bool | None = None,
        responds_to_video_note: bool | None = None,
        responds_to_sticker: bool | None = None,
        transcribe_voice_only: bool | None = None,
        transcribe_video_note: bool | None = None,
        auto_commit: bool = False
    ) -> bool:
        """Update user settings.
        
        Args:
            user_id: The internal ID of the user to update
            is_global_disabled: Whether all responses are disabled
            responds_to_text: Whether to respond to text messages
            responds_to_voice: Whether to respond to voice messages
            responds_to_photo: Whether to respond to photos
            responds_to_video_note: Whether to respond to video notes
            responds_to_sticker: Whether to respond to stickers
            transcribe_voice_only: Whether to only transcribe voice messages
            transcribe_video_note: Whether to transcribe video notes
            auto_commit: Whether to automatically commit the transaction.
                         Set to False when called within a transaction context manager.
        """
        try:
            update_data = {}
            if is_global_disabled is not None:
                update_data["is_global_disabled"] = is_global_disabled
            if responds_to_text is not None:
                update_data["responds_to_text"] = responds_to_text
            if responds_to_voice is not None:
                update_data["responds_to_voice"] = responds_to_voice
            if responds_to_photo is not None:
                update_data["responds_to_photo"] = responds_to_photo
            if responds_to_video_note is not None:
                update_data["responds_to_video_note"] = responds_to_video_note
            if responds_to_sticker is not None:
                update_data["responds_to_sticker"] = responds_to_sticker
            if transcribe_voice_only is not None:
                update_data["transcribe_voice_only"] = transcribe_voice_only
            if transcribe_video_note is not None:
                update_data["transcribe_video_note"] = transcribe_video_note

            if not update_data:
                return True

            stmt = update(User).where(User.id == user_id).values(**update_data)
            await self.session.execute(stmt)
            
            # Only commit if auto_commit is True (not within a transaction context)
            if auto_commit:
                await self.session.commit()
                
            return True
        except SQLAlchemyError as e:
            logger.error(f"Error updating user settings for user_id={user_id}: {e}", exc_info=True)
            if auto_commit:
                await self.session.rollback()
            return False