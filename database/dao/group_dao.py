# services/database/group_dao.py
import logging
from typing import Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..models import Group

logger = logging.getLogger(__name__)

class GroupDAO:
    """Асинхронный DAO для работы с моделью Group."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_group_by_internal_id(self, group_id: int) -> Optional[Group]:
        try:
            stmt = select(Group).where(Group.id == group_id)
            result = await self.session.execute(stmt)
            return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            logger.critical(f"Error getting group by internal id={group_id}: {e}", exc_info=True)
            raise

    async def get_group_by_telegram_id(self, telegram_chat_id: int) -> Optional[Group]:
        try:
            stmt = select(Group).where(Group.telegram_chat_id == telegram_chat_id)
            result = await self.session.execute(stmt)
            return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            logger.critical(f"Error getting group by telegram_chat_id={telegram_chat_id}: {e}", exc_info=True)
            raise

    async def get_or_create_group(self, telegram_chat_id: int, name: str) -> Group:
        values_to_insert = {
            "telegram_chat_id": telegram_chat_id,
            "name": name
        }
        values_to_update = {"name": name}

        insert_stmt = pg_insert(Group).values(**values_to_insert).on_conflict_do_update(
            index_elements=['telegram_chat_id'],
            set_=values_to_update
        ).returning(Group)

        try:
            result = await self.session.execute(insert_stmt)
            return result.scalar_one()
        except SQLAlchemyError as e:
            logger.critical(f"Database error during get_or_create_group for telegram_chat_id={telegram_chat_id}: {e}", exc_info=True)
            raise

    async def update_group_settings(
        self,
        group_id: int,
        is_global_disabled: bool | None = None,
        responds_to_text: bool | None = None,
        responds_to_voice: bool | None = None,
        responds_to_photo: bool | None = None,
        responds_to_video_note: bool | None = None,
        responds_to_sticker: bool | None = None,
        transcribe_voice_only: bool | None = None,
        transcribe_video_note: bool | None = None
    ) -> bool:
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

            stmt = update(Group).where(Group.id == group_id).values(**update_data)
            await self.session.execute(stmt)
            await self.session.commit()
            return True
        except SQLAlchemyError as e:
            logger.error(f"Error updating group settings for group_id={group_id}: {e}", exc_info=True)
            await self.session.rollback()
            return False