from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from services.database.models import MessageHistory, User
from typing import Optional, List
from datetime import datetime
from sqlalchemy import select
from google.genai import types


class DAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_message(self, user_id: int, role: str, text: str = None, audio_data: bytes = None, image_data: bytes = None, video_data: bytes = None) -> Optional[MessageHistory]:
        """Adds a new message to the message history."""
        try:
            new_message = MessageHistory(
                user_id=user_id,
                role=role,
                text=text,
                audio_data=audio_data,
                image_data=image_data,
                video_data=video_data,
                timestamp=datetime.utcnow(),
            )
            self.session.add(new_message)
            await self.session.commit()
            await self.session.refresh(new_message)
            return new_message 
        except SQLAlchemyError:
            await self.session.rollback() 
            return None

    async def get_message(self, message_id: int) -> Optional[MessageHistory]:
        """Retrieves a message by its ID."""
        try:
            result = await self.session.get(MessageHistory, message_id)
            return result
        except SQLAlchemyError:
            return None

    async def get_user_messages_as_contents(self, user_id: int) -> List[types.Content]:
        """Retrieves all messages for a given user and formats them as a list of types.Content."""
        try:
            stmt = select(MessageHistory).where(MessageHistory.user_id == user_id)
            result = await self.session.execute(stmt)
            messages: List[MessageHistory] = result.scalars().all()

            contents: List[types.Content] = []
            for message in messages:
                if message.text:
                    part = types.Part.from_text(text=message.text)
                elif message.audio_data:
                    part = types.Part.from_bytes(data=message.audio_data, mime_type="audio/ogg")
                else:
                    part = None 

                if part:
                    contents.append(
                        types.Content(
                            role=message.role,
                            parts=[part],
                        )
                    )
                else:
                    print(f"Skipping message {message.id} due to missing text and audio data.")

            return contents
        except SQLAlchemyError:
            return []

    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """
        Get user by telegram id
        :param telegram_id: telegram id
        :return: user
        """
        try:
            stmt = select(User).where(User.telegram_id == telegram_id)
            result = await self.session.execute(stmt)
            return result.scalar_one_or_none()
        except SQLAlchemyError:
            return None

    async def create_user(self, username: str, telegram_id: int = None, first_name: str = None, last_name: str = None) -> Optional[User]:
        """Creates a new user."""
        try:
            new_user = User(
                username=username,
                telegram_id=telegram_id,
                first_name=first_name,
                last_name=last_name
            )
            self.session.add(new_user)
            await self.session.commit()
            await self.session.refresh(new_user)
            return new_user
        except SQLAlchemyError:
            await self.session.rollback()
            return None

    async def clear_history(self, user_id: int):
        """Clear message history for a specific user."""
        try:
            stmt = select(MessageHistory).where(MessageHistory.user_id == user_id)
            result = await self.session.execute(stmt)
            messages = result.scalars().all()
            for message in messages:
                await self.session.delete(message)
            await self.session.commit()
        except SQLAlchemyError:
            await self.session.rollback()