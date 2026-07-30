import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class WorldMemoryService:
    """
    Manages Mia's persistent world knowledge — facts she's researched,
    observations she's made, things she wants to remember about the world.
    Stored in MongoDB 'world_memory' collection.
    """

    def __init__(self, db_manager):
        self._db = db_manager
        self._collection = db_manager.db['world_memory']

    async def setup_indexes(self):
        """Create indexes for world_memory collection."""
        try:
            await self._collection.create_index([("created_at", -1)])
            await self._collection.create_index([("compressed", 1), ("created_at", -1)])
            logger.info("World memory indexes created.")
        except Exception as e:
            logger.error(f"Failed to create world memory indexes: {e}")

    async def add_entry(self, topic: str, content: str, source_urls: List[str] = None,
                        entry_type: str = "research") -> Optional[str]:
        """Add a new knowledge entry. Returns the inserted ID or None."""
        try:
            doc = {
                "type": entry_type,
                "topic": topic,
                "content": content,
                "source_urls": source_urls or [],
                "created_at": datetime.utcnow(),
                "compressed": False,
                "char_count": len(content)
            }
            result = await self._collection.insert_one(doc)
            logger.info(f"World memory: added entry '{topic}' ({len(content)} chars)")

            # Check if compression is needed
            settings = await self._db.get_system_settings()
            proactive_cfg = settings.get("proactive", {})
            max_entries = proactive_cfg.get("world_memory_max_entries", 50)
            await self._compress_if_needed(max_entries)

            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Failed to add world memory entry: {e}")
            return None

    async def _compress_if_needed(self, max_entries: int = 50):
        """If total entries exceed max, summarize the oldest non-compressed ones."""
        try:
            total = await self._collection.count_documents({})
            if total <= max_entries:
                return

            # Fetch oldest non-compressed entries
            oldest = await self._collection.find(
                {"compressed": False}
            ).sort("created_at", 1).limit(20).to_list(None)

            if len(oldest) < 5:
                # Not enough to compress meaningfully
                return

            # Build text to summarize
            texts = []
            ids_to_delete = []
            for entry in oldest:
                texts.append(f"[{entry.get('topic', 'Unknown')}]: {entry.get('content', '')}")
                ids_to_delete.append(entry["_id"])

            combined = "\n\n".join(texts)

            # Use gatekeeper's summarize capability
            from services.gatekeeper_service import get_gatekeeper
            gatekeeper = get_gatekeeper()
            summary = await gatekeeper.summarize_text(combined)

            if summary and summary != "Summary failed.":
                # Delete old entries and insert compressed summary
                await self._collection.delete_many({"_id": {"$in": ids_to_delete}})

                date_range = f"{oldest[0]['created_at'].strftime('%Y-%m-%d')} - {oldest[-1]['created_at'].strftime('%Y-%m-%d')}"
                await self._collection.insert_one({
                    "type": "summary",
                    "topic": f"Compressed memory [{date_range}]",
                    "content": summary,
                    "source_urls": [],
                    "created_at": datetime.utcnow(),
                    "compressed": True,
                    "char_count": len(summary)
                })
                logger.info(f"World memory: compressed {len(ids_to_delete)} entries into 1 summary")
        except Exception as e:
            logger.error(f"World memory compression failed: {e}", exc_info=True)

    async def get_memory_for_injection(self, max_chars: int = 8000) -> str:
        """
        Build a formatted string of world knowledge for injection into AI prompt.
        Recent non-compressed entries first, then compressed summaries.
        Truncates to max_chars.
        """
        try:
            # First, get recent non-compressed entries
            recent = await self._collection.find(
                {"compressed": False}
            ).sort("created_at", -1).limit(30).to_list(None)

            # Then, get compressed summaries
            summaries = await self._collection.find(
                {"compressed": True}
            ).sort("created_at", -1).limit(10).to_list(None)

            parts = []
            total_chars = 0

            # Add recent entries first (most valuable)
            for entry in recent:
                line = f"- [{entry.get('topic', '?')}]: {entry.get('content', '')}"
                if total_chars + len(line) > max_chars:
                    break
                parts.append(line)
                total_chars += len(line)

            # Add summaries for older context
            for entry in summaries:
                line = f"- [Summary]: {entry.get('content', '')}"
                if total_chars + len(line) > max_chars:
                    break
                parts.append(line)
                total_chars += len(line)

            return "\n".join(parts) if parts else ""
        except Exception as e:
            logger.error(f"Failed to get world memory for injection: {e}")
            return ""

    async def get_recent_topics(self, n: int = 15) -> List[str]:
        """Return topic names of the N most recent entries to avoid redundant research."""
        try:
            entries = await self._collection.find(
                {"compressed": False},
                {"topic": 1}
            ).sort("created_at", -1).limit(n).to_list(None)
            return [e.get("topic", "") for e in entries if e.get("topic")]
        except Exception as e:
            logger.error(f"Failed to get recent topics: {e}")
            return []

    async def get_all_entries(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return all entries for admin panel display."""
        try:
            entries = await self._collection.find().sort("created_at", -1).limit(limit).to_list(None)
            for e in entries:
                e["_id"] = str(e["_id"])
            return entries
        except Exception as e:
            logger.error(f"Failed to list world memory: {e}")
            return []

    async def clear_all(self):
        """Delete all world memory entries."""
        await self._collection.delete_many({})
        logger.info("World memory: cleared all entries")
