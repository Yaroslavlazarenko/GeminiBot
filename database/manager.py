import logging
from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, uri: str, db_name: str):
        self.client = AsyncIOMotorClient(uri)
        self.db = self.client[db_name]
        self.users = self.db['users']
        self.groups = self.db['groups']

    async def _setup_indexes(self):
        """Create necessary indexes for performance."""
        try:
            await self.users.create_index("telegram_id", unique=True)
            await self.groups.create_index("telegram_chat_id", unique=True)
            logger.info("MongoDB indexes created successfully.")
        except Exception as e:
            logger.error(f"Error creating MongoDB indexes: {e}")

    async def connect(self):
        """Verify connection and setup indexes."""
        try:
            # Ping the server to verify connection
            await self.client.admin.command('ping')
            logger.info("Successfully connected to MongoDB.")
            await self._setup_indexes()
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise

    async def close(self):
        """Close the database connection."""
        self.client.close()
        logger.info("MongoDB connection closed.")

    # --- User Methods ---

    async def get_or_create_user(self, telegram_id: int, username: str = None, first_name: str = None, last_name: str = None) -> Dict[str, Any]:
        """Get a user document, creating it with default settings if it doesn't exist."""
        user = await self.users.find_one({"telegram_id": telegram_id})
        if not user:
            user = {
                "telegram_id": telegram_id,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "settings": {
                    "is_global_disabled": False,
                    "responds_to_text": True,
                    "responds_to_voice": True,
                    "responds_to_photo": True,
                    "responds_to_video_note": True,
                    "responds_to_sticker": True,
                    "transcribe_voice_only": False,
                    "transcribe_video_note": False,
                },
                "history": []
            }
            await self.users.insert_one(user)
        return user

    async def update_user_settings(self, telegram_id: int, settings: Dict[str, Any]):
        """Update user settings."""
        await self.users.update_one(
            {"telegram_id": telegram_id},
            {"$set": {f"settings.{k}": v for k, v in settings.items()}}
        )

    async def append_user_history(self, telegram_id: int, message: Dict[str, Any], max_history: int = 50):
        """Append a message to the user's history, keeping only the latest max_history messages."""
        await self.users.update_one(
            {"telegram_id": telegram_id},
            {
                "$push": {
                    "history": {
                        "$each": [message],
                        "$slice": -max_history
                    }
                }
            }
        )
        
    async def clear_user_history(self, telegram_id: int):
        """Clear the message history for a user."""
        await self.users.update_one(
            {"telegram_id": telegram_id},
            {"$set": {"history": []}}
        )

    # --- Group Methods ---

    async def get_or_create_group(self, telegram_chat_id: int, name: str) -> Dict[str, Any]:
        """Get a group document, creating it with default settings if it doesn't exist."""
        group = await self.groups.find_one({"telegram_chat_id": telegram_chat_id})
        if not group:
            group = {
                "telegram_chat_id": telegram_chat_id,
                "name": name,
                "settings": {
                    "is_global_disabled": False,
                    "responds_to_text": True,
                    "responds_to_voice": True,
                    "responds_to_photo": True,
                    "responds_to_video_note": True,
                    "responds_to_sticker": True,
                    "transcribe_voice_only": False,
                    "transcribe_video_note": False,
                },
                "history": []
            }
            await self.groups.insert_one(group)
        return group

    async def update_group_settings(self, telegram_chat_id: int, settings: Dict[str, Any]):
        """Update group settings."""
        await self.groups.update_one(
            {"telegram_chat_id": telegram_chat_id},
            {"$set": {f"settings.{k}": v for k, v in settings.items()}}
        )

    async def append_group_history(self, telegram_chat_id: int, message: Dict[str, Any], max_history: int = 50):
        """Append a message to the group's history, keeping only the latest max_history messages."""
        await self.groups.update_one(
            {"telegram_chat_id": telegram_chat_id},
            {
                "$push": {
                    "history": {
                        "$each": [message],
                        "$slice": -max_history
                    }
                }
            }
        )

    async def clear_group_history(self, telegram_chat_id: int):
        """Clear the message history for a group."""
        await self.groups.update_one(
            {"telegram_chat_id": telegram_chat_id},
            {"$set": {"history": []}}
        )
