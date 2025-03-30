from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession  # Import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from services.database.models import MessageHistory, User
from typing import Optional, List
from datetime import datetime
from sqlalchemy import select
from google.genai import types


class DAO:
    def __init__(self, session: AsyncSession):  # Use AsyncSession
        self.session = session  # Store the session

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
            return new_message  # Return the newly created message
        except SQLAlchemyError as e:
            print(f"Error adding message: {e}")
            await self.session.rollback()  # Rollback in case of error
            return None

    async def get_message(self, message_id: int) -> Optional[MessageHistory]:
        """Retrieves a message by its ID."""
        try:
            result = await self.session.get(MessageHistory, message_id)  # Use session.get
            return result
        except SQLAlchemyError as e:
            print(f"Error getting message: {e}")
            return None

    async def get_user_messages_as_contents(self, user_id: int) -> List[types.Content]:
        """Retrieves all messages for a given user and formats them as a list of types.Content."""
        try:
            stmt = select(MessageHistory).where(MessageHistory.user_id == user_id)
            result = await self.session.execute(stmt)
            messages: List[MessageHistory] = result.scalars().all()

            contents: List[types.Content] = []
            for message in messages:
                # Create a Part object based on whether text or audio data is available
                if message.text:
                    part = types.Part.from_text(text=message.text)
                elif message.audio_data:
                    part = types.Part.from_bytes(data=message.audio_data, mime_type="audio/ogg")
                else:
                    part = None  # Handle cases where neither text nor audio is available

                if part:
                    contents.append(
                        types.Content(
                            role=message.role,
                            parts=[part],  # Wrap the Part object in a list
                        )
                    )
                else:
                    print(f"Skipping message {message.id} due to missing text and audio data.")

            return contents
        except SQLAlchemyError as e:
            print(f"Error getting user messages: {e}")
            return []

    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """
        Get user by telegram id
        :param telegram_id: telegram id
        :return: user
        """
        try:
            stmt = select(User).where(User.telegram_id == telegram_id)  # Construct a Select object
            result = await self.session.execute(stmt)  # Execute the statement
            return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            print(f"Error getting user by telegram_id: {e}")
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
            await self.session.refresh(new_user) # Refresh to load generated ID
            return new_user  # Return the newly created user
        except SQLAlchemyError as e:
            print(f"Error creating user: {e}")
            await self.session.rollback() # Rollback in case of error
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
        except SQLAlchemyError as e:
            print(f"Error clearing history: {e}")
            await self.session.rollback()