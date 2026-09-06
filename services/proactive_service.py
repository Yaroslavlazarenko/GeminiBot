import logging
import random
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from google.genai.types import GenerateContentConfig, Content, Part, Tool, AutomaticFunctionCallingConfig
from pydantic import BaseModel, Field

from core.key_manager import get_key_manager
from services.world_memory_service import WorldMemoryService

logger = logging.getLogger(__name__)


class ResearchDecision(BaseModel):
    search_query: str = Field(description="A concise search query to research this topic via web search.")
    topic_label: str = Field(description="A short label (3-6 words) describing the topic for memory storage.")
    reasoning: str = Field(description="Why Mia finds this topic interesting right now.")


class ProactiveMessageDecision(BaseModel):
    should_message: bool = Field(description="Whether Mia actually wants to write to this person/group right now.")
    message: str = Field(default="", description="The message text to send. Empty if should_message is False.")
    reasoning: str = Field(description="Brief reasoning for the decision.")


class ProactiveService:
    """
    Handles all proactive AI behaviors:
    1. Web research — Mia decides what to search, processes results, saves to world memory
    2. Proactive messaging — Mia decides whether and what to write to users/groups
    """

    def __init__(self, db_manager, bot, world_memory_service: WorldMemoryService):
        self._db = db_manager
        self._bot = bot
        self._world_memory = world_memory_service
        self._key_manager = get_key_manager()

    async def _get_settings(self) -> Dict[str, Any]:
        settings = await self._db.get_system_settings()
        return settings.get("proactive", {})

    async def _get_system_instruction(self) -> str:
        settings = await self._db.get_system_settings()
        return settings.get("system_instruction", "You are Mia Zareva.")

    async def _get_model(self) -> str:
        settings = await self._db.get_system_settings()
        return settings.get("gemini_api_model") or "gemini-2.5-flash"

    # =====================================================================
    # RESEARCH
    # =====================================================================

    async def do_research_cycle(self) -> bool:
        """
        Execute one research cycle:
        1. Ask Mia to pick a topic
        2. Search via Exa MCP
        3. Summarize and save to world memory
        Returns True if research was successful.
        """
        try:
            cfg = await self._get_settings()
            recent_topics = await self._world_memory.get_recent_topics(15)
            system_instruction = await self._get_system_instruction()
            model = await self._get_model()

            # Step 1: Pick a topic
            decision = await self._pick_research_topic(
                system_instruction, model, recent_topics,
                cfg.get("research_topics_seed", "")
            )
            if not decision:
                logger.warning("Proactive research: failed to pick a topic")
                return False

            logger.info(f"Proactive research: picked topic '{decision.topic_label}' — query: '{decision.search_query}'")

            # Step 2: Search via MCP (Exa)
            search_results = await self._execute_exa_search(decision.search_query)
            if not search_results:
                logger.warning("Proactive research: Exa search returned no results")
                return False

            # Step 3: Summarize findings
            digest = await self._summarize_research(
                system_instruction, model,
                decision.topic_label, decision.search_query, search_results
            )
            if not digest:
                logger.warning("Proactive research: failed to summarize results")
                return False

            # Step 4: Save to world memory
            # Extract URLs from search results if available
            source_urls = self._extract_urls(search_results)
            await self._world_memory.add_entry(
                topic=decision.topic_label,
                content=digest,
                source_urls=source_urls,
                entry_type="research"
            )

            logger.info(f"Proactive research complete: '{decision.topic_label}' ({len(digest)} chars)")
            return True

        except Exception as e:
            logger.error(f"Proactive research cycle failed: {e}", exc_info=True)
            return False

    async def _pick_research_topic(self, system_instruction: str, model: str,
                                     recent_topics: List[str], seed_topics: str) -> Optional[ResearchDecision]:
        """Ask Mia to pick an interesting topic to research."""
        try:
            from zoneinfo import ZoneInfo
            odessa_tz = ZoneInfo("Europe/Kyiv")
        except Exception:
            odessa_tz = timezone(timedelta(hours=3))
        now = datetime.now(odessa_tz)
        time_str = now.strftime("%Y-%m-%d %H:%M (%A)")

        recent_str = ", ".join(recent_topics) if recent_topics else "Nothing yet — this is your first research!"

        prompt = (
            f"Current time: {time_str}\n\n"
            f"You recently researched these topics (do NOT repeat them):\n{recent_str}\n\n"
        )
        if seed_topics:
            prompt += f"Your general interests: {seed_topics}\n\n"

        prompt += (
            "Pick ONE topic you'd genuinely like to research right now. "
            "It should be something current, interesting, or useful. "
            "Think about what's happening in the world, what you're curious about, "
            "or what might be useful to know for conversations with people.\n\n"
            "Return a search query and a short topic label."
        )

        try:
            response = self._key_manager.generate_content(
                model=model,
                contents=[Content(role="user", parts=[Part(text=prompt)])],
                config=GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.9,
                    response_mime_type="application/json",
                    response_schema=ResearchDecision
                )
            )
            if response.parsed:
                return response.parsed
            # Fallback: try to parse text manually
            if response.text:
                import json
                data = json.loads(response.text)
                return ResearchDecision(**data)
        except Exception as e:
            logger.error(f"Failed to pick research topic: {e}")
        return None

    async def _execute_exa_search(self, query: str) -> Optional[str]:
        """Execute a search via MCP search tools (Exa / web search)."""
        try:
            from services.ai_service import get_ai_service
            ai_service = get_ai_service()

            # Ensure MCP is connected
            await ai_service.mcp_manager.connect()

            if not ai_service.mcp_manager.adapters_map:
                logger.warning("Proactive research: No MCP adapters available")
                return None

            # Filter out image-only search tools (e.g. reverse_image_search, google_images)
            IMAGE_SEARCH_KEYWORDS = ["image", "photo", "reverse_image", "vision", "picture"]

            search_tool_name = None
            # 1. Prefer Grok / Exa / dedicated web search tools
            for tool_name in ai_service.mcp_manager.adapters_map:
                tn_lower = tool_name.lower()
                if any(kw in tn_lower for kw in IMAGE_SEARCH_KEYWORDS):
                    continue
                if "grok" in tn_lower or "ask_grok" in tn_lower:
                    search_tool_name = tool_name
                    break
                elif "search" in tn_lower and "exa" in tn_lower:
                    search_tool_name = tool_name
                    break
                elif "web_search" in tn_lower:
                    search_tool_name = tool_name
                    break

            # 2. Try any other general web/text search tool
            if not search_tool_name:
                for tool_name in ai_service.mcp_manager.adapters_map:
                    tn_lower = tool_name.lower()
                    if any(kw in tn_lower for kw in IMAGE_SEARCH_KEYWORDS):
                        continue
                    if "search" in tn_lower or "tavily" in tn_lower or "brave" in tn_lower or "duckduckgo" in tn_lower:
                        search_tool_name = tool_name
                        break

            if not search_tool_name:
                logger.warning("Proactive research: No web/text search tool found in MCP adapters (ignoring image-only tools)")
                return None

            # Create a mock FunctionCall to use the MCP adapter
            from google.genai.types import FunctionCall
            search_args: Dict[str, Any] = {"query": query}
            if "exa" in search_tool_name.lower():
                search_args["numResults"] = 3
            search_call = FunctionCall(
                name=search_tool_name,
                args=search_args
            )

            adapter = ai_service.mcp_manager.adapters_map[search_tool_name]
            parts = await adapter.process_function_calls_as_parts([search_call])

            if parts:
                # Extract text content from the response part
                part = parts[0]
                if hasattr(part, 'function_response') and part.function_response:
                    response_data = part.function_response.response
                    if isinstance(response_data, dict):
                        if response_data.get("error"):
                            logger.warning(f"Proactive research: MCP search tool '{search_tool_name}' returned error: {response_data.get('error')}")
                            return None
                        result = response_data.get("result") or ""
                        if isinstance(result, str):
                            return result
                        elif isinstance(result, (dict, list)):
                            import json
                            return json.dumps(result, ensure_ascii=False, indent=2)[:8000]
                    elif isinstance(response_data, str):
                        return response_data
            return None
        except Exception as e:
            logger.error(f"MCP search failed: {e}", exc_info=True)
            return None

    async def _summarize_research(self, system_instruction: str, model: str,
                                    topic: str, query: str, raw_results: str) -> Optional[str]:
        """Summarize raw search results into a concise knowledge entry."""
        prompt = (
            f"You just searched the web for: \"{query}\" (topic: {topic})\n\n"
            f"Here are the raw results:\n{raw_results[:6000]}\n\n"
            "Summarize the key findings into a concise paragraph (3-5 sentences). "
            "Focus on interesting facts, current events, or useful information. "
            "Write in the same language as the search results if they're in Russian/Ukrainian, otherwise in Russian. "
            "Do NOT include filler words or meta-commentary — just the facts."
        )
        try:
            response = self._key_manager.generate_content(
                model=model,
                contents=[Content(role="user", parts=[Part(text=prompt)])],
                config=GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.4,
                )
            )
            return response.text if response.text else None
        except Exception as e:
            logger.error(f"Failed to summarize research: {e}")
            return None

    def _extract_urls(self, raw_results: str) -> List[str]:
        """Extract URLs from raw search results."""
        import re
        urls = re.findall(r'https?://[^\s\'"<>]+', raw_results)
        return urls[:10]  # Cap at 10

    # =====================================================================
    # PROACTIVE MESSAGING
    # =====================================================================

    async def do_messaging_cycle(self) -> int:
        """
        Check all chats (users + groups) and decide whether to message them.
        Returns the number of messages sent.
        """
        sent_count = 0
        try:
            cfg = await self._get_settings()
            if not cfg.get("messaging_enabled", True):
                return 0

            system_instruction = await self._get_system_instruction()
            model = await self._get_model()

            # Get world memory snippet for context
            world_memory_text = await self._world_memory.get_memory_for_injection(
                max_chars=cfg.get("world_memory_max_chars", 4000)
            )

            # Process private chats
            users = await self._get_eligible_chats(is_group=False, cfg=cfg)
            for chat_doc in users:
                try:
                    if await self._try_send_proactive(chat_doc, is_group=False,
                                                       system_instruction=system_instruction,
                                                       model=model,
                                                       world_memory_text=world_memory_text, cfg=cfg):
                        sent_count += 1
                        import asyncio
                        await asyncio.sleep(2)  # Avoid Telegram rate limits
                except Exception as e:
                    chat_id = chat_doc.get("telegram_id") or chat_doc.get("telegram_chat_id")
                    logger.error(f"Proactive messaging error for chat {chat_id}: {e}")

            # Process groups
            groups = await self._get_eligible_chats(is_group=True, cfg=cfg)
            for chat_doc in groups:
                try:
                    if await self._try_send_proactive(chat_doc, is_group=True,
                                                       system_instruction=system_instruction,
                                                       model=model,
                                                       world_memory_text=world_memory_text, cfg=cfg):
                        sent_count += 1
                        import asyncio
                        await asyncio.sleep(2)
                except Exception as e:
                    chat_id = chat_doc.get("telegram_chat_id")
                    logger.error(f"Proactive messaging error for group {chat_id}: {e}")

            logger.info(f"Proactive messaging cycle complete: {sent_count} messages sent")
            return sent_count

        except Exception as e:
            logger.error(f"Proactive messaging cycle failed: {e}", exc_info=True)
            return sent_count

    async def _get_eligible_chats(self, is_group: bool, cfg: dict) -> List[Dict[str, Any]]:
        """Get all chats that pass the basic eligibility filter."""
        min_silence_hours = cfg.get("messaging_min_silence_hours", 12)
        max_ignored = cfg.get("messaging_max_consecutive_ignored", 2)

        cutoff = datetime.utcnow() - timedelta(hours=min_silence_hours)

        if is_group:
            collection = self._db.groups
            id_field = "telegram_chat_id"
        else:
            collection = self._db.users
            id_field = "telegram_id"

        # Find chats where:
        # 1. Has history (we know who they are)
        # 2. Not globally disabled or proactively disabled
        # 3. Not already awaiting a reply
        # 4. Haven't exceeded max consecutive ignored
        # 5. Last user activity was long enough ago (silence period)
        chats = await collection.find({
            "history": {"$exists": True, "$ne": []},
            "is_active": {"$ne": False},
            "settings.is_global_disabled": {"$ne": True},
            "proactive.disabled": {"$ne": True},
            "proactive.awaiting_reply": {"$ne": True},
            "proactive.consecutive_ignored": {"$not": {"$gte": max_ignored}},
            "$or": [
                {"proactive.last_user_message_at": {"$exists": False}},
                {"proactive.last_user_message_at": None},
                {"proactive.last_user_message_at": {"$lte": cutoff}}
            ]
        }).to_list(None)

        # Apply probability filter
        probability = cfg.get("messaging_probability", 0.3)
        filtered = [c for c in chats if random.random() < probability]

        logger.info(f"Proactive messaging: {len(chats)} eligible {'groups' if is_group else 'users'}, "
                     f"{len(filtered)} passed probability filter ({probability})")
        return filtered

    async def _try_send_proactive(self, chat_doc: dict, is_group: bool,
                                    system_instruction: str, model: str,
                                    world_memory_text: str, cfg: dict) -> bool:
        """Try to generate and send a proactive message to one chat. Returns True if sent."""
        if is_group:
            chat_id = chat_doc.get("telegram_chat_id")
            chat_name = chat_doc.get("name", "Unknown Group")
            id_field = "telegram_chat_id"
            collection = self._db.groups
        else:
            chat_id = chat_doc.get("telegram_id")
            first = chat_doc.get("first_name", "")
            last = chat_doc.get("last_name", "")
            chat_name = f"{first} {last}".strip() or "Unknown User"
            id_field = "telegram_id"
            collection = self._db.users

        # Get recent history (last 10 messages for context)
        history = chat_doc.get("history", [])[-10:]
        if not history:
            return False

        history_text = ""
        for msg in history:
            role = msg.get("role", "unknown")
            text = msg.get("text", "")
            ts = msg.get("timestamp", "")
            if text:
                history_text += f"[{ts}] {role}: {text}\n"

        # Get user facts for private chats
        facts_text = ""
        if not is_group:
            try:
                facts = await self._db.get_user_facts(chat_id, chat_id)
                if facts:
                    facts_text = "\n".join([f"- {f['fact']}" for f in facts])
            except Exception:
                pass

        # Build the prompt for Mia
        try:
            from zoneinfo import ZoneInfo
            odessa_tz = ZoneInfo("Europe/Kyiv")
        except Exception:
            odessa_tz = timezone(timedelta(hours=3))
        now = datetime.now(odessa_tz)
        time_str = now.strftime("%Y-%m-%d %H:%M (%A)")

        chat_type = "group chat" if is_group else "private chat"
        prompt = (
            f"Current time: {time_str}\n\n"
            f"You are thinking about whether to proactively write to someone.\n"
            f"Chat type: {chat_type}\n"
            f"Chat name: {chat_name}\n\n"
        )

        if facts_text:
            prompt += f"Known facts about this person:\n{facts_text}\n\n"

        prompt += f"Recent conversation history:\n{history_text}\n\n"

        if world_memory_text:
            prompt += (
                f"Your recent world knowledge (things you've learned from research):\n"
                f"{world_memory_text[:3000]}\n\n"
            )

        prompt += (
            "Based on the conversation history and your world knowledge, decide whether to write something.\n"
            "Reasons to write:\n"
            "- You found something interesting that's relevant to what you were discussing\n"
            "- You want to ask about something, follow up on a topic, or share a thought\n"
            "- You genuinely want to continue the conversation naturally\n"
            "- You remembered something or learned something new that connects to this chat\n\n"
            "Reasons NOT to write:\n"
            "- The conversation ended naturally and there's nothing to add\n"
            "- It would feel forced or spammy\n"
            "- You have nothing genuinely interesting or relevant to say\n\n"
        )

        if is_group:
            prompt += (
                "IMPORTANT: For group chats, only write if you have something truly relevant "
                "to the group's recent discussion or if you found interesting info related to it. "
                "Do NOT write just to say hi or ask generic questions in groups.\n\n"
            )

        prompt += (
            "If you decide to write, make it natural and conversational — "
            "as if you just thought of something and decided to share. "
            "Don't be formal, don't start with greetings like 'hey' unless it fits naturally."
        )

        # Acquire per-chat generation lock — same lock used by user-message handlers
        # to prevent concurrent AI generation + history writes for the same chat.
        from bot.handlers import _get_generation_lock
        lock = _get_generation_lock(chat_id)
        async with lock:
            try:
                response = self._key_manager.generate_content(
                    model=model,
                    contents=[Content(role="user", parts=[Part(text=prompt)])],
                    config=GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=0.8,
                        response_mime_type="application/json",
                        response_schema=ProactiveMessageDecision
                    )
                )

                decision = None
                if response.parsed:
                    decision = response.parsed
                elif response.text:
                    import json
                    data = json.loads(response.text)
                    decision = ProactiveMessageDecision(**data)

                if not decision or not decision.should_message or not decision.message.strip():
                    logger.info(f"Proactive: Mia chose NOT to message {chat_name}"
                               f" (reason: {decision.reasoning if decision else 'no decision'})")
                    return False

                # Send the message
                message_text = decision.message.strip()
                logger.info(f"Proactive: Mia sending message to {chat_name}: {message_text[:100]}...")

                sent_msg = await self._bot.send_message(chat_id=chat_id, text=message_text)

                # Save to history
                from core.database import ChatContext
                doc = chat_doc
                ctx = ChatContext(self._db, chat_id, is_group, doc)
                await ctx.add_message("model", message_text, sent_msg.message_id)

                # Update proactive state
                now_utc = datetime.utcnow()
                proactive_state = chat_doc.get("proactive", {})
                consecutive = proactive_state.get("consecutive_ignored", 0)

                # If previous message was also unanswered, increment
                if proactive_state.get("awaiting_reply", False):
                    consecutive += 1

                await collection.update_one(
                    {id_field: chat_id},
                    {"$set": {
                        "proactive.last_proactive_sent_at": now_utc,
                        "proactive.last_proactive_message_id": sent_msg.message_id,
                        "proactive.awaiting_reply": True,
                        "proactive.consecutive_ignored": consecutive
                    }}
                )

                logger.info(f"Proactive message sent to {chat_name} (msg_id: {sent_msg.message_id})")
                return True

            except Exception as e:
                err_str = str(e)
                # Check for permanent permission/access errors so we don't retry endlessly
                if any(kw in err_str.lower() for kw in [
                    "not enough rights", "forbidden", "blocked", "chat not found",
                    "user is deactivated", "bot was kicked", "have no rights",
                    "need administrator rights"
                ]):
                    logger.warning(
                        f"Proactive: bot has no rights to message {chat_name} (chat_id: {chat_id}): {e}. "
                        "Disabling proactive messaging for this chat."
                    )
                    try:
                        await collection.update_one(
                            {id_field: chat_id},
                            {"$set": {
                                "proactive.disabled": True,
                                "proactive.disabled_reason": err_str,
                                "proactive.awaiting_reply": False,
                                "proactive.consecutive_ignored": 999
                            }}
                        )
                    except Exception as db_err:
                        logger.error(f"Failed to update proactive disabled state for {chat_id}: {db_err}")
                else:
                    logger.error(f"Failed to generate/send proactive message to {chat_name}: {e}", exc_info=True)
                return False
