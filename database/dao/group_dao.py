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
        logger.debug(f"Getting group by internal DB id={group_id}")
        try:
            stmt = select(Group).where(Group.id == group_id)
            result = await self.session.execute(stmt)
            group = result.scalar_one_or_none()
            if group: logger.debug(f"Group found by internal id: {group.id=}, {group.telegram_chat_id=}, {group.name=}")
            else: logger.debug(f"Group not found for internal id={group_id}")
            return group
        except SQLAlchemyError as e:
            logger.error(f"Error getting group by internal id={group_id}: {e}", exc_info=True)
            raise

    async def get_group_by_telegram_id(self, telegram_chat_id: int) -> Optional[Group]:
        logger.debug(f"Getting group by telegram_chat_id={telegram_chat_id}")
        try:
            stmt = select(Group).where(Group.telegram_chat_id == telegram_chat_id)
            result = await self.session.execute(stmt)
            group = result.scalar_one_or_none()
            if group: logger.debug(f"Group found for telegram_chat_id={telegram_chat_id}: {group.id=}, {group.name=}")
            else: logger.debug(f"Group not found for telegram_chat_id={telegram_chat_id}")
            return group
        except SQLAlchemyError as e:
            logger.error(f"Error getting group by telegram_chat_id={telegram_chat_id}: {e}", exc_info=True)
            raise

    async def get_or_create_group(self, telegram_chat_id: int, name: str) -> Group:
        logger.debug(f"Attempting to get or create/update group for telegram_chat_id={telegram_chat_id}")
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
            group = result.scalar_one()
            logger.info(f"Successfully got or created/updated group: {group.id=} {group.telegram_chat_id=} {group.name=}")
            return group
        except SQLAlchemyError as e:
            logger.error(f"Database error during get_or_create_group for telegram_chat_id={telegram_chat_id}: {e}", exc_info=True)
            raise

    async def update_group_settings(self, group_id: int, responds_to_text: bool | None = None, responds_to_voice: bool | None = None) -> bool:
        logger.debug(f"Updating settings for group_id={group_id}")
        values_to_update = {}
        if responds_to_text is not None: values_to_update["responds_to_text"] = responds_to_text
        if responds_to_voice is not None: values_to_update["responds_to_voice"] = responds_to_voice

        if not values_to_update:
            logger.warning(f"No settings provided to update for group_id={group_id}")
            return False

        stmt = update(Group).where(Group.id == group_id).values(**values_to_update)
        try:
            result = await self.session.execute(stmt)
            if result.rowcount > 0:
                logger.info(f"Successfully updated settings for group_id={group_id}")
                return True
            else:
                logger.warning(f"Group with internal id={group_id} not found for settings update or settings unchanged.")
                group_exists = await self.session.get(Group, group_id)
                return group_exists is not None
        except SQLAlchemyError as e:
            logger.error(f"Database error updating settings for group_id={group_id}: {e}", exc_info=True)
            raise