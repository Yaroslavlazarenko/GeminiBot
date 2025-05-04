import logging
from typing import Optional, List
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from ..models import Sticker

logger = logging.getLogger(__name__)

class StickerDAO:
    """Асинхронный DAO для работы с моделью Sticker."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_sticker_by_id(self, sticker_id: int) -> Optional[Sticker]:
        try:
            stmt = select(Sticker).where(Sticker.id == sticker_id)
            result = await self.session.execute(stmt)
            return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            logger.error(f"Error getting sticker by id={sticker_id}: {e}", exc_info=True)
            raise

    async def get_sticker_by_telegram_id(self, telegram_sticker_id: str) -> Optional[Sticker]:
        try:
            stmt = select(Sticker).where(Sticker.telegram_sticker_id == telegram_sticker_id)
            result = await self.session.execute(stmt)
            return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            logger.error(f"Error getting sticker by telegram_id={telegram_sticker_id}: {e}", exc_info=True)
            raise

    async def create_sticker(
        self,
        telegram_sticker_id: str,
        telegram_message_id: int | None,
        name: str | None,
        emoji: str | None,
        image_data: bytes
    ) -> Sticker:
        """Create a new sticker entry."""
        try:
            sticker = Sticker(
                telegram_sticker_id=telegram_sticker_id,
                telegram_message_id=telegram_message_id,
                name=name,
                emoji=emoji,
                image_data=image_data
            )
            self.session.add(sticker)
            await self.session.flush()  # To get the generated ID
            return sticker
        except SQLAlchemyError as e:
            logger.error(f"Error creating sticker with telegram_id={telegram_sticker_id}: {e}", exc_info=True)
            raise

    async def get_or_create_sticker(
        self,
        telegram_sticker_id: str,
        telegram_message_id: int | None,
        name: str | None,
        emoji: str | None,
        image_data: bytes
    ) -> Sticker:
        """Get existing sticker or create a new one."""
        try:
            existing_sticker = await self.get_sticker_by_telegram_id(telegram_sticker_id)
            if existing_sticker:
                return existing_sticker
            
            return await self.create_sticker(
                telegram_sticker_id=telegram_sticker_id,
                telegram_message_id=telegram_message_id,
                name=name,
                emoji=emoji,
                image_data=image_data
            )
        except SQLAlchemyError as e:
            logger.error(f"Error in get_or_create_sticker for telegram_id={telegram_sticker_id}: {e}", exc_info=True)
            raise