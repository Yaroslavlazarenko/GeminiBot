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

    async def add_message(self, role: str, text: str, message_id: int, timestamp: str = None, reactions: list = None, pending: bool = False):
        """Add a message to the history and permanent log.

        If pending=True, the message is saved to DB immediately (crash-safe),
        but marked as pending in-memory so _convert_history_to_gemini() skips it.
        Call confirm_message() after generation to clear the pending flag.
        """
        msg = {"role": role, "text": text, "message_id": message_id}
        if timestamp:
            msg["timestamp"] = timestamp
        if reactions:
            msg["reactions"] = reactions
        if pending:
            msg["pending"] = True

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

        # Strip the pending flag before writing to DB — it's only for in-memory use
        db_msg = {k: v for k, v in msg.items() if k != "pending"}
        if self.is_group:
            await self._db.append_group_history(self.id, db_msg)
        else:
            await self._db.append_user_history(self.id, db_msg)
        self.history.append(msg)

    def confirm_message(self, message_id: int):
        """Remove the pending flag from a message in-memory after generation completes.

        This is a pure in-memory operation — the DB copy was already written
        correctly without the pending flag.
        """
        for msg in self.history:
            if msg.get("message_id") == message_id and msg.get("pending"):
                msg.pop("pending", None)
                break

    async def update_settings(self, settings: Dict[str, Any]):
        """Update settings for the current context."""
        if self.is_group:
            await self._db.update_group_settings(self.id, settings)
        else:
            await self._db.update_user_settings(self.id, settings)
        self.settings.update(settings)

    async def clear_history(self):
        """Clear the entire history for the current context."""
        if self.is_group:
            await self._db.clear_group_history(self.id)
        else:
            await self._db.clear_user_history(self.id)
        self.history = []

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
        self.world_memory = self.db['world_memory']
        self.auto_memory = self.db['auto_memory']
        self.scheduled_tasks = self.db['scheduled_tasks']

    async def _setup_indexes(self):
        """Create necessary indexes for performance."""
        try:
            await self.users.create_index("telegram_id", unique=True)
            await self.groups.create_index("telegram_chat_id", unique=True)
            await self.messages.create_index([("chat_id", 1), ("date", -1)])
            await self.messages.create_index([("text", "text")])
            await self.world_memory.create_index([("created_at", -1)])
            await self.world_memory.create_index([("compressed", 1), ("created_at", -1)])
            await self.auto_memory.create_index([("chat_id", 1), ("updated_at", -1)])
            await self.auto_memory.create_index([("chat_id", 1), ("topic", 1)], unique=True)
            await self.scheduled_tasks.create_index([("chat_id", 1), ("status", 1)])
            await self.scheduled_tasks.create_index([("status", 1), ("next_run_at", 1)])
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

    async def save_user_fact(self, telegram_id: int, fact: str, source: str, chat_id: int, is_global: bool = False, category: str = "other", confidence: float = 1.0):
        """Save a persistent fact about a user. Scoped by chat_id to prevent leaking private info to groups."""
        await self.users.update_one(
            {"telegram_id": telegram_id},
            {"$push": {
                "facts": {
                    "fact": fact,
                    "source": source,
                    "chat_id": chat_id,
                    "is_global": is_global,
                    "category": category,
                    "confidence": confidence,
                    "date": datetime.utcnow(),
                    "last_confirmed": datetime.utcnow(),
                    "superseded_by": None
                }
            }}
        )

    async def get_user_facts(self, telegram_id: int, current_chat_id: int) -> List[Dict[str, Any]]:
        """Retrieve facts about a user that are either global or explicitly scoped to the current chat.
        Filters out superseded facts and sorts by last_confirmed descending."""
        user = await self.users.find_one({"telegram_id": telegram_id})
        if not user:
            return []

        all_facts = user.get("facts", [])
        # Filter facts: Only return if it was learned in this exact chat, OR if it's explicitly marked as harmless/global
        # Also filter out superseded facts
        filtered = []
        for f in all_facts:
            if f.get("superseded_by") is not None:
                continue
            if f.get("chat_id") == current_chat_id or f.get("is_global", False):
                filtered.append(f)

        # Sort by last_confirmed descending (fallback to date for old facts)
        def sort_key(f):
            return f.get("last_confirmed") or f.get("date") or datetime.min
        filtered.sort(key=sort_key, reverse=True)

        return filtered

    async def supersede_user_fact(self, telegram_id: int, old_fact_text: str, new_fact_text: str, chat_id: int):
        """Mark an existing fact as superseded by a new contradicting fact.
        Uses arrayFilters to find and update the specific fact in the array."""
        try:
            await self.users.update_one(
                {"telegram_id": telegram_id},
                {"$set": {
                    "facts.$[elem].superseded_by": new_fact_text,
                }},
                array_filters=[{
                    "elem.fact": old_fact_text,
                    "elem.superseded_by": None
                }]
            )
            logger.info(f"Superseded fact for user {telegram_id}: '{old_fact_text[:50]}...' -> '{new_fact_text[:50]}...'")
        except Exception as e:
            logger.error(f"Failed to supersede fact for user {telegram_id}: {e}")

    async def confirm_user_fact(self, telegram_id: int, fact_text: str, chat_id: int):
        """Update last_confirmed timestamp for an existing fact."""
        try:
            await self.users.update_one(
                {"telegram_id": telegram_id},
                {"$set": {
                    "facts.$[elem].last_confirmed": datetime.utcnow()
                }},
                array_filters=[{
                    "elem.fact": fact_text,
                    "elem.superseded_by": None
                }]
            )
            logger.info(f"Confirmed fact for user {telegram_id}: '{fact_text[:50]}...'")
        except Exception as e:
            logger.error(f"Failed to confirm fact for user {telegram_id}: {e}")

    async def append_user_history(self, telegram_id: int, message: Dict[str, Any], max_history: int = 200):
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

    async def append_group_history(self, telegram_chat_id: int, message: Dict[str, Any], max_history: int = 200):
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

    # --- Auto Memory (Mia's personal notes per chat) ---

    async def save_auto_memory(self, chat_id: int, user_id: int, topic: str, content: str, max_entries: int = 30):
        """Save or update a personal memory note. Upserts by (chat_id, topic).
        If max_entries is exceeded, removes the least recently used entry."""
        now = datetime.utcnow()
        result = await self.auto_memory.update_one(
            {"chat_id": chat_id, "topic": topic},
            {
                "$set": {
                    "content": content,
                    "updated_at": now,
                    "user_id": user_id,
                },
                "$setOnInsert": {
                    "created_at": now,
                    "access_count": 0,
                }
            },
            upsert=True
        )

        # Enforce max entries per chat — remove LRU (oldest updated_at, lowest access_count)
        try:
            count = await self.auto_memory.count_documents({"chat_id": chat_id})
            if count > max_entries:
                # Find the least valuable entry: sort by access_count ASC, then updated_at ASC
                lru = await self.auto_memory.find(
                    {"chat_id": chat_id}
                ).sort([("access_count", 1), ("updated_at", 1)]).limit(1).to_list(1)
                if lru:
                    await self.auto_memory.delete_one({"_id": lru[0]["_id"]})
                    logger.info(f"Auto memory: evicted LRU entry '{lru[0].get('topic')}' for chat {chat_id}")
        except Exception as e:
            logger.error(f"Failed to enforce auto memory limit: {e}")

        action = "updated" if result.matched_count > 0 else "created"
        logger.info(f"Auto memory: {action} '{topic}' for chat {chat_id}")

    async def recall_auto_memory(self, chat_id: int, topic: str) -> Optional[Dict[str, Any]]:
        """Recall a memory by topic (case-insensitive partial match).
        Increments access_count and updates updated_at on access."""
        import re
        # Try exact match first, then partial
        entry = await self.auto_memory.find_one({"chat_id": chat_id, "topic": topic})
        if not entry:
            # Case-insensitive partial match
            try:
                pattern = re.compile(re.escape(topic), re.IGNORECASE)
                entry = await self.auto_memory.find_one({"chat_id": chat_id, "topic": pattern})
            except Exception:
                pass

        if entry:
            # Update access stats
            await self.auto_memory.update_one(
                {"_id": entry["_id"]},
                {
                    "$inc": {"access_count": 1},
                    "$set": {"updated_at": datetime.utcnow()}
                }
            )
            return entry
        return None

    async def get_auto_memory_topics(self, chat_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        """Return topic labels for injection into the system prompt."""
        entries = await self.auto_memory.find(
            {"chat_id": chat_id},
            {"topic": 1, "updated_at": 1, "_id": 0}
        ).sort("updated_at", -1).limit(limit).to_list(None)
        return entries

    # --- Proactive Messaging State ---

    async def mark_chat_activity(self, chat_id: int, is_group: bool):
        """
        Called when a user/group sends any message to the bot.
        Resets the proactive 'awaiting_reply' flag and updates last activity time.
        """
        collection = self.groups if is_group else self.users
        query_field = "telegram_chat_id" if is_group else "telegram_id"

        await collection.update_one(
            {query_field: chat_id},
            {"$set": {
                "proactive.awaiting_reply": False,
                "proactive.last_user_message_at": datetime.utcnow(),
                "proactive.consecutive_ignored": 0
            }}
        )

    # --- Scheduled Tasks Management ---

    MAX_SCHEDULED_TASKS_PER_CHAT = 5
    MIN_RECURRING_INTERVAL_MINUTES = 30

    async def create_scheduled_task(
        self,
        chat_id: int,
        creator_user_id: int,
        creator_name: str,
        task_description: str,
        delay_minutes: Optional[int] = None,
        run_at_datetime: Optional[str] = None,
        is_recurring: bool = False,
        interval_minutes: Optional[int] = None,
        is_group: bool = False
    ) -> Dict[str, Any]:
        """Create a scheduled task with chat limit and recurring interval enforcement."""
        import secrets
        from datetime import timezone, timedelta
        from zoneinfo import ZoneInfo
        ODESSA_TZ = ZoneInfo("Europe/Kyiv")

        # 1. Limit per chat check
        active_count = await self.scheduled_tasks.count_documents({
            "chat_id": chat_id,
            "status": "active"
        })
        if active_count >= self.MAX_SCHEDULED_TASKS_PER_CHAT:
            return {
                "error": f"Достигнут лимит активных задач для этого чата (максимум {self.MAX_SCHEDULED_TASKS_PER_CHAT}). "
                         f"Удалите неактуальные задачи через delete_scheduled_task, чтобы создать новую."
            }

        # 2. Enforce minimum interval for recurring tasks
        if is_recurring:
            if not interval_minutes or interval_minutes < self.MIN_RECURRING_INTERVAL_MINUTES:
                interval_minutes = max(int(interval_minutes or 60), self.MIN_RECURRING_INTERVAL_MINUTES)

        # 3. Calculate initial execution time (next_run_at)
        now_utc = datetime.now(timezone.utc)
        next_run_at = None

        if delay_minutes is not None and delay_minutes > 0:
            next_run_at = now_utc + timedelta(minutes=delay_minutes)
        elif run_at_datetime:
            clean = run_at_datetime.strip()
            # Handle "HH:MM"
            if len(clean) <= 5 and ":" in clean:
                try:
                    parts = clean.split(":")
                    h, m = int(parts[0]), int(parts[1])
                    now_local = datetime.now(ODESSA_TZ)
                    target = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
                    if target <= now_local:
                        target += timedelta(days=1)
                    next_run_at = target.astimezone(timezone.utc)
                except Exception:
                    pass
            else:
                try:
                    clean_iso = clean.replace(" ", "T")
                    dt = datetime.fromisoformat(clean_iso)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=ODESSA_TZ)
                    next_run_at = dt.astimezone(timezone.utc)
                except Exception:
                    pass

        if not next_run_at:
            # Fallback default: interval or 10 minutes
            default_delay = interval_minutes if (is_recurring and interval_minutes) else 10
            next_run_at = now_utc + timedelta(minutes=default_delay)

        # Ensure future time
        if next_run_at <= now_utc:
            next_run_at = now_utc + timedelta(minutes=5)

        task_id = f"task_{secrets.token_hex(3)}"
        task_doc = {
            "_id": task_id,
            "chat_id": chat_id,
            "is_group": is_group,
            "creator_user_id": creator_user_id,
            "creator_name": creator_name,
            "task_description": task_description,
            "is_recurring": is_recurring,
            "interval_minutes": interval_minutes if is_recurring else None,
            "next_run_at": next_run_at,
            "last_run_at": None,
            "created_at": datetime.now(timezone.utc),
            "status": "active",
            "execution_count": 0,
            "last_error": None
        }

        await self.scheduled_tasks.insert_one(task_doc)
        local_run_str = next_run_at.astimezone(ODESSA_TZ).strftime("%Y-%m-%d %H:%M")

        return {
            "result": f"Задача успешно создана и добавлена в расписание!",
            "task_id": task_id,
            "task_description": task_description,
            "next_run_at": f"{local_run_str} (время Одессы)",
            "is_recurring": is_recurring,
            "interval_minutes": interval_minutes if is_recurring else None,
            "active_tasks_in_chat": active_count + 1,
            "max_tasks_allowed": self.MAX_SCHEDULED_TASKS_PER_CHAT
        }

    async def get_scheduled_tasks(self, chat_id: int, status: str = "active") -> List[Dict[str, Any]]:
        """Retrieve scheduled tasks for a specific chat."""
        cursor = self.scheduled_tasks.find({
            "chat_id": chat_id,
            "status": status
        }).sort("next_run_at", 1)
        return await cursor.to_list(None)

    async def delete_scheduled_task(self, chat_id: int, task_id: str) -> bool:
        """Cancel and delete a scheduled task by ID in the given chat."""
        result = await self.scheduled_tasks.update_one(
            {"_id": task_id, "chat_id": chat_id, "status": "active"},
            {"$set": {"status": "cancelled", "cancelled_at": datetime.utcnow()}}
        )
        return result.modified_count > 0

    async def get_due_scheduled_tasks(self, now: datetime) -> List[Dict[str, Any]]:
        """Find active tasks whose scheduled run time has arrived."""
        cursor = self.scheduled_tasks.find({
            "status": "active",
            "next_run_at": {"$lte": now}
        }).sort("next_run_at", 1).limit(20)
        return await cursor.to_list(None)

    async def update_scheduled_task_after_run(
        self,
        task_id: str,
        next_run_at: Optional[datetime],
        is_recurring: bool,
        error: Optional[str] = None
    ):
        """Update task state after an execution cycle."""
        now = datetime.utcnow()
        update_doc: Dict[str, Any] = {
            "$inc": {"execution_count": 1},
            "$set": {
                "last_run_at": now,
                "last_error": error
            }
        }
        if is_recurring and next_run_at:
            update_doc["$set"]["next_run_at"] = next_run_at
        else:
            update_doc["$set"]["status"] = "completed"

        await self.scheduled_tasks.update_one({"_id": task_id}, update_doc)

    async def get_chat_context(self, chat_id: int) -> Optional[ChatContext]:
        """Resolve a unified ChatContext instance for either a group or a private user."""
        if chat_id < 0:
            doc = await self.groups.find_one({"telegram_chat_id": chat_id})
            if not doc:
                doc = await self.get_or_create_group(chat_id, "Group")
            return ChatContext(self, chat_id, True, doc)
        else:
            doc = await self.users.find_one({"telegram_id": chat_id})
            if not doc:
                doc = await self.get_or_create_user(chat_id)
            return ChatContext(self, chat_id, False, doc)
