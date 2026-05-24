import logging
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

class ChatContext:
    """A unified interface for interacting with the current chat context (User or Group)."""
    def __init__(self, db_manager, context_id: int, is_group: bool, document: Dict[str, Any]):
        self._db = db_manager
        self.id = context_id
        self.is_group = is_group
        self.doc = document
        self.settings = document.get("settings", {})
        self.history = document.get("history", [])

    @property
    def is_disabled(self) -> bool:
        return self.settings.get("is_global_disabled", False)

    def responds_to(self, msg_type: str) -> bool:
        """Check if the context allows responding to a specific message type (e.g., 'text', 'voice')."""
        return self.settings.get(f"responds_to_{msg_type}", True)

    async def add_message(self, role: str, text: str, message_id: int, timestamp: str = None, reactions: list = None):
        """Add a message to the history and permanent log."""
        msg = {"role": role, "text": text, "message_id": message_id}
        if timestamp:
            msg["timestamp"] = timestamp
        if reactions:
            msg["reactions"] = reactions
            
        # Permanent storage
        perm_msg = {
            "chat_id": self.id,
            "role": role,
            "text": text,
            "message_id": message_id,
            "date": datetime.utcnow(),
            "timestamp_str": timestamp
        }
        await self._db.messages.insert_one(perm_msg)
            
        if self.is_group:
            await self._db.append_group_history(self.id, msg)
        else:
            await self._db.append_user_history(self.id, msg)
        self.history.append(msg)

    async def update_settings(self, settings: Dict[str, Any]):
        """Update settings for the current context."""
        if self.is_group:
            await self._db.update_group_settings(self.id, settings)
        else:
            await self._db.update_user_settings(self.id, settings)
        self.settings.update(settings)

    async def replace_history(self, new_history: list):
        """Replace the entire history with a summarized version."""
        if self.is_group:
            await self._db.groups.update_one(
                {"telegram_chat_id": self.id},
                {"$set": {"history": new_history}}
            )
        else:
            await self._db.users.update_one(
                {"telegram_id": self.id},
                {"$set": {"history": new_history}}
            )
        self.history = new_history

    async def update_message_reactions(self, message_id: int, reactions: list):
        """Update reactions for a message in local memory and database."""
        # Update in-memory
        for msg in self.history:
            if msg.get("message_id") == message_id:
                msg["reactions"] = reactions
                break
        # Update in database
        await self._db.update_message_reactions(self.id, self.is_group, message_id, reactions)


class DatabaseManager:
    def __init__(self, uri: str, db_name: str):
        self.client = AsyncIOMotorClient(uri)
        self.db = self.client[db_name]
        self.users = self.db['users']
        self.groups = self.db['groups']
        self.stickers = self.db['stickers']
        self.messages = self.db['messages']

    async def _setup_indexes(self):
        """Create necessary indexes for performance."""
        try:
            await self.users.create_index("telegram_id", unique=True)
            await self.groups.create_index("telegram_chat_id", unique=True)
            await self.messages.create_index([("chat_id", 1), ("date", -1)])
            await self.messages.create_index([("text", "text")])
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

    # --- System Settings ---
    async def get_system_settings(self) -> Dict[str, Any]:
        """Get the global system settings, merging DB overrides with Config defaults if needed."""
        settings = await self.db['system_settings'].find_one({"_id": "global"})
        
        default_avatar_prompt = (
            "Опиши эту аватарку пользователя в Telegram в 2-3 предложениях. "
            "Что на ней изображено, какой стиль, цвета, атмосфера? "
            "Пиши от первого лица (ты — Мия Zareva), как будто ты сама смотришь на неё. "
            "Будь дружелюбной и подмечай интересные детали."
        )
        
        if not settings:
            try:
                with open("system_instructions.md", "r", encoding="utf-8") as f:
                    default_prompt = f.read()
            except Exception:
                default_prompt = "You are Mia Zareva."

            settings = {
                "_id": "global",
                "gemini_api_model": "",
                "gemini_gatekeeper_model": "",
                "gemini_base_url": "",
                "gemini_api_key": "",
                "gemini_api_keys": "",
                "mcp_servers_config": "{}",
                "system_instruction": default_prompt,
                "sticker_set_name": "MelieTheCat",
                "avatar_prompt": default_avatar_prompt
            }
            await self.db['system_settings'].insert_one(settings)
        else:
            # Ensure defaults exist
            updates = {}
            if "sticker_set_name" not in settings:
                updates["sticker_set_name"] = "MelieTheCat"
                settings["sticker_set_name"] = "MelieTheCat"
            if "avatar_prompt" not in settings:
                updates["avatar_prompt"] = default_avatar_prompt
                settings["avatar_prompt"] = default_avatar_prompt
            if "gemini_api_key" not in settings:
                updates["gemini_api_key"] = ""
                settings["gemini_api_key"] = ""
            if "gemini_api_keys" not in settings:
                updates["gemini_api_keys"] = ""
                settings["gemini_api_keys"] = ""
                
            if updates:
                await self.db['system_settings'].update_one({"_id": "global"}, {"$set": updates})
        return settings

    async def update_system_settings(self, updates: Dict[str, Any]):
        """Update the global system settings."""
        await self.db['system_settings'].update_one(
            {"_id": "global"},
            {"$set": updates},
            upsert=True
        )

    # --- User Methods ---

    async def get_or_create_user(self, telegram_id: int, username: str = None, first_name: str = None, last_name: str = None) -> Dict[str, Any]:
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
                },
                "avatar_file_unique_id": None,
                "avatar_description": None,
                "avatar_last_checked": None,
                "history": []
            }
            await self.users.insert_one(user)
        else:
            # Dynamically update user metadata if changed
            updates = {}
            if user.get("username") != username:
                updates["username"] = username
            if user.get("first_name") != first_name:
                updates["first_name"] = first_name
            if user.get("last_name") != last_name:
                updates["last_name"] = last_name
            
            if updates:
                await self.users.update_one({"telegram_id": telegram_id}, {"$set": updates})
                user.update(updates)
        return user

    async def update_user_settings(self, telegram_id: int, settings: Dict[str, Any]):
        await self.users.update_one(
            {"telegram_id": telegram_id},
            {"$set": {f"settings.{k}": v for k, v in settings.items()}}
        )

    async def save_user_fact(self, telegram_id: int, fact: str, source: str):
        """Save a persistent fact about a user."""
        await self.users.update_one(
            {"telegram_id": telegram_id},
            {"$push": {
                "facts": {
                    "fact": fact,
                    "source": source,
                    "date": datetime.utcnow()
                }
            }}
        )

    async def get_user_facts(self, telegram_id: int) -> List[Dict[str, Any]]:
        """Retrieve all persistent facts about a user."""
        user = await self.users.find_one({"telegram_id": telegram_id})
        return user.get("facts", []) if user else []

    async def append_user_history(self, telegram_id: int, message: Dict[str, Any], max_history: int = 50):
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
        await self.users.update_one(
            {"telegram_id": telegram_id},
            {"$set": {"history": []}}
        )

    # --- Group Methods ---

    async def get_or_create_group(self, telegram_chat_id: int, name: str) -> Dict[str, Any]:
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
                },
                "history": []
            }
            await self.groups.insert_one(group)
        else:
            if group.get("name") != name:
                await self.groups.update_one({"telegram_chat_id": telegram_chat_id}, {"$set": {"name": name}})
                group["name"] = name
        return group

    async def update_group_settings(self, telegram_chat_id: int, settings: Dict[str, Any]):
        await self.groups.update_one(
            {"telegram_chat_id": telegram_chat_id},
            {"$set": {f"settings.{k}": v for k, v in settings.items()}}
        )

    async def append_group_history(self, telegram_chat_id: int, message: Dict[str, Any], max_history: int = 50):
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
        await self.groups.update_one(
            {"telegram_chat_id": telegram_chat_id},
            {"$set": {"history": []}}
        )

    async def update_message_reactions(self, chat_id: int, is_group: bool, message_id: int, reactions: list):
        """Update reactions for a specific message in history."""
        collection = self.groups if is_group else self.users
        query_field = "telegram_chat_id" if is_group else "telegram_id"
        
        # Positional operator $ updates the specific element in the 'history' array matching message_id
        await collection.update_one(
            {query_field: chat_id, "history.message_id": message_id},
            {"$set": {"history.$.reactions": reactions}}
        )
