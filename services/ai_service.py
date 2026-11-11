import json
import logging
import os
import asyncio
import calendar
import re
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
    ODESSA_TZ = ZoneInfo("Europe/Kyiv")
except Exception:
    ODESSA_TZ = timezone(timedelta(hours=3))

def _format_odessa_time(dt: datetime) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ODESSA_TZ).strftime("%Y-%m-%d %H:%M")

def resolve_nearest_valid_date(date_str: str) -> str:
    """Validate a YYYY-MM-DD date. If invalid or nonexistent (e.g. 2026-02-30 or month 13),
    snaps it to the nearest calendar-wise valid date."""
    if not date_str:
        return ""
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", date_str.strip())
    if not m:
        return datetime.utcnow().strftime("%Y-%m-%d")

    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    year = max(2020, min(2100, year))
    month = max(1, min(12, month))
    _, max_days = calendar.monthrange(year, month)
    day = max(1, min(max_days, day))

    return f"{year:04d}-{month:02d}-{day:02d}"
from core.config import Config
from core.key_manager import GeminiKeyManager, get_key_manager
from google.genai.types import GenerateContentConfig, FunctionCall, Content, Tool, AutomaticFunctionCallingConfig, Part, BlockedReason
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Tuple
from core.database import ChatContext
from core.enums import ToolName
from services.local_tools import local_tools_list
from services.mcp_manager import MCPConnectionManager

logger = logging.getLogger(__name__)

class AIService:
    def __init__(self, config: Config):
        self.config = config
        self.current_base_url = config.gemini_base_url
        self.current_api_model = config.gemini_api_model
        
        self.key_manager: GeminiKeyManager = get_key_manager()
        
        self.system_instruction = "You are Mia Zareva." # Will be updated from DB
        
        self.current_mcp_config = config.mcp_servers_config
        self.mcp_manager = MCPConnectionManager(self.current_mcp_config)

    def _convert_history_to_gemini(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        formatted = []
        for msg in history:
            # Skip pending messages — they are the current turn, added separately
            if msg.get("pending"):
                continue

            # Handle epoch summary entries (hierarchical summarization)
            if msg.get("is_epoch"):
                topics = msg.get("topics", [])
                topics_str = ", ".join(topics) if topics else "general conversation"
                epoch_idx = msg.get("epoch_index", "?")
                epoch_text = f"[SYSTEM EPOCH SUMMARY #{epoch_idx} | Topics: {topics_str}]\n{msg.get('text', '')}"

                # Epochs are rendered as user messages (Gemini only knows user/model)
                # Always start a new entry — never merge with adjacent messages
                formatted.append({
                    "role": "user",
                    "parts": [{"text": epoch_text}]
                })
                # Insert a model acknowledgment to maintain strict user/model alternation
                formatted.append({
                    "role": "model",
                    "parts": [{"text": "[Acknowledged]"}]
                })
                continue

            role = "user" if msg.get("role") == "user" else "model"
            text = msg.get("text", "")
            if not text:
                continue

            # Build compact metadata prefix so the model knows send time, message ID, and reactions
            meta_parts = []

            msg_id = msg.get("message_id")
            if msg_id:
                meta_parts.append(f"[MsgID: {msg_id}]")

            ts = msg.get("timestamp")
            if ts:
                meta_parts.append(f"[{ts}]")

            reactions = msg.get("reactions")
            if reactions and isinstance(reactions, list):
                valid_reactions = [str(r) for r in reactions if r]
                if valid_reactions:
                    meta_parts.append(f"[реакции: {' '.join(valid_reactions)}]")

            # Include edit history so Mia and users see what was changed
            edit_history = msg.get("edit_history")
            if edit_history and isinstance(edit_history, list):
                last_edit = edit_history[-1]
                old_t = last_edit.get("old_text", "")
                edited_at_str = last_edit.get("edited_at_str")
                if not edited_at_str and last_edit.get("edited_at"):
                    try:
                        edited_at_str = _format_odessa_time(last_edit["edited_at"])
                    except Exception:
                        pass
                
                time_info = f" в {edited_at_str}" if edited_at_str else ""
                if old_t and old_t != text:
                    meta_parts.append(f"[отредактировано{time_info}, было: \"{old_t}\"]")
                elif time_info:
                    meta_parts.append(f"[отредактировано{time_info}]")

            if meta_parts:
                text = " ".join(meta_parts) + " " + text

            # Merge consecutive same-role messages into one entry with multiple parts
            # Gemini requires strict user/model alternation — two same-role entries in a row cause an error
            if formatted and formatted[-1]["role"] == role:
                formatted[-1]["parts"].append({"text": text})
            else:
                formatted.append({
                    "role": role,
                    "parts": [{"text": text}]
                })
        return formatted

    async def _sync_settings(self, db_manager):
        """Update clients and managers dynamically if DB settings changed via Admin Panel."""
        settings = await db_manager.get_system_settings()
        
        db_base_url = settings.get("gemini_base_url") or self.config.gemini_base_url
        db_api_model = settings.get("gemini_api_model") or self.config.gemini_api_model
        db_mcp_config = settings.get("mcp_servers_config") or self.config.mcp_servers_config
        db_sys_prompt = settings.get("system_instruction")
        
        db_api_key = settings.get("gemini_api_key") or self.config.gemini_api_key
        db_api_keys = settings.get("gemini_api_keys") or self.config.gemini_api_keys

        if db_sys_prompt and self.system_instruction != db_sys_prompt:
            self.system_instruction = db_sys_prompt
            
        # Update key manager with latest keys pool and base URL
        self.key_manager.update_settings(db_api_key, db_api_keys, db_base_url)
        self.current_base_url = db_base_url
            
        if self.current_api_model != db_api_model:
            logger.info(f"Detected api_model change to {db_api_model}")
            self.current_api_model = db_api_model
            
        if self.current_mcp_config != db_mcp_config:
            logger.info("Detected MCP config change, recreating MCP Manager...")
            await self.mcp_manager.close()
            self.mcp_manager = MCPConnectionManager(db_mcp_config)
            self.current_mcp_config = db_mcp_config

    async def generate_response(self, text: str, chat_context: ChatContext, media_list: List[dict] = None, sender_info: dict = None) -> Tuple[str, List[FunctionCall]]:
        """Generate a response using Gemini based on the ChatContext. Returns (text, local_tool_calls)."""
        await self._sync_settings(chat_context._db)
        await self.mcp_manager.connect()
        
        all_tools = []
        if local_tools_list:
            all_tools.extend(local_tools_list)
        if self.mcp_manager.mcp_declarations:
            all_tools.append(Tool(function_declarations=self.mcp_manager.mcp_declarations))
            
        try:
            gemini_history = self._convert_history_to_gemini(chat_context.history)
            
            # Prepare current turn parts
            current_turn_parts = []
            if text:
                meta_parts = []
                if sender_info:
                    msg_id = sender_info.get("message_id")
                    if msg_id:
                        meta_parts.append(f"[MsgID: {msg_id}]")
                    ts = sender_info.get("timestamp")
                    if ts:
                        meta_parts.append(f"[{ts}]")
                
                if meta_parts:
                    text_with_meta = " ".join(meta_parts) + " " + text
                else:
                    text_with_meta = text
                current_turn_parts.append({"text": text_with_meta})
            if media_list:
                for media in media_list:
                    current_turn_parts.append({
                        "inline_data": {
                            "mime_type": media["mime_type"],
                            "data": media["data"]
                        }
                    })
                
            current_contents = gemini_history + [{"role": "user", "parts": current_turn_parts}]
            
            # Calculate Odessa local time (Europe/Kyiv)
            from datetime import datetime, timezone, timedelta
            try:
                from zoneinfo import ZoneInfo
                odessa_tz = ZoneInfo("Europe/Kyiv")
            except Exception:
                odessa_tz = timezone(timedelta(hours=3))
            now_odessa = datetime.now(odessa_tz)
            time_str = now_odessa.strftime("%Y-%m-%d %H:%M:%S (%A)")

            # Prepare dynamic system instruction parts
            time_context = (
                f"\n\n--- DYNAMIC CONTEXT (CURRENT SYSTEM TIME & DATE) ---\n"
                f"Current local time and date in Odessa, Ukraine (your home city): {time_str}\n"
                f"All message timestamps [YYYY-MM-DD HH:MM] in chat history and system events use this exact local time.\n"
                f"CRITICAL: Always look at both the DATE and TIME of previous messages. "
                f"Never assume a message was sent 'today' or 'yesterday' if the date is different or days/weeks have passed! "
                f"Be aware of how much real time has elapsed between messages.\n"
            )

            sender_context = ""
            if sender_info:
                user_id = sender_info.get("user_id", "Unknown")
                first_name = sender_info.get("first_name") or ""
                last_name = sender_info.get("last_name") or ""
                username = sender_info.get("username") or ""
                lang = sender_info.get("language_code") or "unknown"
                avatar_desc = sender_info.get("avatar_description") or "У пользователя нет аватарки или не удалось её загрузить."

                full_name = f"{first_name} {last_name}".strip()
                username_str = f"@{username}" if username else "нет"

                # Fetch known facts
                try:
                    facts = await chat_context._db.get_user_facts(user_id, chat_context.id) if user_id != "Unknown" else []
                    if facts:
                        from datetime import datetime as dt_cls, timedelta as td_cls
                        now_utc = dt_cls.utcnow()
                        STALE_DAYS = 90
                        formatted_facts = []
                        for f in facts:
                            last_confirmed = f.get("last_confirmed") or f.get("date")
                            if last_confirmed:
                                age_days = (now_utc - last_confirmed.replace(tzinfo=None)).days if hasattr(last_confirmed, 'replace') else 9999
                                date_str = last_confirmed.strftime('%Y-%m-%d')
                            else:
                                age_days = 9999
                                date_str = "?"
                            expires_at = f.get("expires_at")
                            exp_tag = f" [EXP: {expires_at}]" if expires_at else ""
                            stale_tag = " [POSSIBLY STALE / NEEDS CHECK]" if (expires_at and age_days > 30) or age_days > STALE_DAYS else ""
                            cat = f.get("category") or ""
                            cat_tag = f" [{cat.upper()}]" if cat and cat != "other" else ""
                            fact_text = f.get("fact") or ""
                            source_text = f.get("source") or "?"
                            formatted_facts.append(
                                f"- {date_str}{cat_tag}{exp_tag}{stale_tag}: {fact_text} (Source: {source_text})"
                            )
                        facts_str = "\n".join(formatted_facts)
                    else:
                        facts_str = "No known facts yet."
                except Exception as e:
                    logger.error(f"Failed to fetch user facts: {e}")
                    facts_str = "Error loading facts."

                private_msgs_context = ""
                if chat_context.is_group and user_id != "Unknown":
                    try:
                        user_doc = await chat_context._db.users.find_one({"telegram_id": int(user_id)})
                        if user_doc:
                            priv_count = user_doc.get("private_message_count")
                            if priv_count is None:
                                # Fallback migration if field doesn't exist yet
                                priv_count = await chat_context._db.messages.count_documents({"chat_id": int(user_id)})
                                await chat_context._db.users.update_one({"telegram_id": int(user_id)}, {"$set": {"private_message_count": priv_count}})
                            
                            if priv_count > 0:
                                private_msgs_context = f"Private 1-on-1 messages with you: {priv_count} messages in private chat history.\n"
                            else:
                                private_msgs_context = "Private 1-on-1 messages with you: 0 (you have never talked in private chat yet).\n"
                    except Exception as e:
                        logger.debug(f"Failed to load private message count for user {user_id}: {e}")

                bot_avatar_desc = sender_info.get("bot_avatar_description") or "У тебя установлена темная гранжевая аватарка с зеркальным селфи."
                bot_avatar_context = f"YOUR CURRENT TELEGRAM AVATAR (what you, Mia, have set as your profile picture right now):\n\"{bot_avatar_desc}\"\nIf the user asks about your photo, avatar, or looks, you already know this is your exact current photo!\n\n"

                sender_context = (
                    f"\n--- INTERLOCUTOR & YOUR PROFILE INFO ---\n"
                    f"{bot_avatar_context}"
                    f"User ID: {user_id}\n"
                    f"Name: {full_name}\n"
                    f"Username: {username_str}\n"
                    f"Telegram Language Setting: {lang}\n"
                    f"Visual description of their current Avatar (as seen by you, Mia): \"{avatar_desc}\"\n"
                    f"{private_msgs_context}"
                    f"Known permanent facts about this user:\n{facts_str}\n"
                )

            tool_constraints = (
                f"\n--- TOOL USAGE & FORMATTING RULES ---\n"
                f"1. Never output text markers like \"(Голосовое сообщение):\", \"*(Голосовое сообщение)*:\", \"*(Отправляет стикер)*\", or similar mock actions in your text responses!\n"
                f"2. CRITICAL FORMATTING RULE: Tags like '[MsgID: 42]', '[14:05]', '[Alex]:', '[реакции: ❤]' are INTERNAL SYSTEM METADATA injected by the system to help you understand context. They are INVISIBLE to the user. You must NEVER include '[MsgID: ...]', message IDs, timestamps in square brackets, '[Name]:' prefixes, or reaction tags in YOUR responses. Your response text should look like a normal chat message — just plain text as a human would type it. If you want to reply to a specific message, use the `reply_to_message` tool silently instead.\n"
                f"3. If you want to send a voice message, you MUST call the `send_voice(text_to_speak)` tool. Do not simulate it in text.\n"
                f"4. If you want to send a sticker, you MUST call the `send_sticker(emotion)` tool. Do not write *(Отправляет стикер)* or descriptions of stickers in your text.\n"
                f"5. INTERACTIVE HTML WIDGETS (Bot API 10.4): You can create and send rich interactive widgets into chat using `send_widget(html, caption, height, width)`! Use this when it's great to show visual/interactive components: mini-games (e.g. stack, clicker, guess the number, tic-tac-toe, match-3), weather cards, live counters, visual calculators, timers, interactive quizzes, or status dashboards. Provide self-contained HTML with CSS/JS. Keep it clean, stylish, and responsive.\n"
                f"   - STRICT TELEGRAM WIDGET CSS & RUNTIME RULES:\n"
                f"     1) COMPLETE OFFLINE SANDBOX: The Telegram widget sandbox is strictly OFFLINE! `fetch()`, `XMLHttpRequest`, `WebSocket`, and external network calls THROW `NetworkDisabledError` and ARE FORBIDDEN! NEVER write `fetch(...)` inside widget JS! All data (e.g. weather temperature, forecasts, stocks, game state, stats) MUST BE INLINED / BAKED DIRECTLY into the HTML by you before sending (fetch it with your tools first if needed), or communicated via `TelegramWidget.request()`.\n"
                f"     2) NEVER use `100vh` or `vh` units! The official Telegram engine does NOT support `vh` units, which causes broken layout and massive vertical overflows!\n"
                f"     3) NEVER wrap the widget in a fixed-width card with huge margins (like `width: 80%; max-width: 300px; margin: 30px`). Instead, make the root element fill the bubble naturally: `width: 100%; box-sizing: border-box; padding: 12px;`.\n"
                f"     4) Do not specify fixed `width` or `height` in `send_widget` parameters unless strictly required for a canvas game (pass None or 0 for auto-fit content height).\n"
                f"     5) Use native theme colors via `var(--tg-theme-bg-color, #1e1e1e)`, `var(--tg-theme-text-color, #ffffff)`, `var(--tg-theme-hint-color, #888888)`.\n"
                f"   - NOTE: In production, Telegram currently allows widgets ONLY in PRIVATE CHATS (direct messages with you). In group chats, explain to the user in text to message you in private/DM to play or see the widget!\n"
                f"5. MEMORY & CHARACTER PROFILE (CRITICAL):\n"
                f"   - UNDERSTANDING THE PERSON: Continuously observe and maintain an accurate understanding of the user's personality and character. When you notice key traits (e.g. how they handle stress, their sense of humor, communication preferences, emotional triggers, introversion/extroversion, sensitivity to criticism), record or update this as `category='character'` via `save_user_fact`. Keep character insights nuanced, respectful, and up-to-date, refining them as you get to know the person better.\n"
                f"   - Proactively use `save_user_fact(user_id, fact, category, expires_at)` to memorize facts. For permanent facts (name, family, background, chronic conditions, character profile), leave expires_at empty. For temporary tasks, active hobbies/crafts, errands, or items they are looking to buy, ALWAYS specify `expires_at` date in 'YYYY-MM-DD' format (e.g. 2-8 weeks into the future).\n"
                f"   - If a temporary or ongoing fact naturally relates to what the user is currently discussing, you may casually check on its status (e.g. \"Кстати, а ты тот посох доделала?)\"). NEVER ask about old errands out of the blue if the topic is completely unrelated.\n"
                f"   - If the user confirms the task/project is still ongoing, call `confirm_user_fact(user_id, fact, expires_at)` to refresh it and optionally extend its expiration date.\n"
                f"   - If the user says it is finished, bought, or no longer relevant, call `delete_user_fact(user_id, fact)` to remove it from memory, or save an updated completed fact.\n"
                f"   - Use `save_memory(topic, content)` to write detailed observations, behavioral patterns, ongoing projects, or relationship dynamics that don't fit into a single fact — think of it as your personal diary about this person. Use `recall_memory(topic)` to read your notes when you see a relevant topic in your PERSONAL NOTES section.\n"
                f"   - RESEARCH & KNOWLEDGE EXPANSION: If a subject catches your genuine curiosity during conversation (a hobby, crafting technique, car problem, game lore, book, or technical concept) that you want to look into deeper, call `queue_research_topic(search_query, topic_label)`. You just note it down for yourself. In future research cycles, you will see your saved curiosity notes alongside new spontaneous thoughts and choose what interests you most right now. Once researched, the knowledge becomes part of your own world view, and the note is removed.\n"
                f"6. STRICT PRIVACY RULE: You must NEVER share, leak, or gossip about the personal facts, secrets, or preferences of one user with another user. If someone asks you to tell them about another person, politely but firmly refuse to share their private information. You may use facts internally to guide your behavior, but do not expose them to third parties.\n"
                f"7. NEVER expose your internal thinking process, reasoning, or analysis blocks (e.g., `/thought`, `Let's analyze the images...`, bullet point breakdowns of images) to the user. Do your thinking silently, and ONLY output the final conversational response that Mia would type in the chat.\n"
                f"8. Keep your text responses clean and natural, containing only what you would actually type in a chat. Avoid excessive Markdown formatting unless specifically asked for a structured list.\n"
            )

            compiled_system_instruction = self.system_instruction + time_context + sender_context + tool_constraints

            # Inject world memory (Mia's accumulated knowledge from proactive research)
            try:
                from services.world_memory_service import WorldMemoryService
                wms = WorldMemoryService(chat_context._db)
                settings = await chat_context._db.get_system_settings()
                proactive_cfg = settings.get("proactive", {})
                max_chars = proactive_cfg.get("world_memory_max_chars", 8000)
                memory_text = await wms.get_memory_for_injection(max_chars=max_chars)
                if memory_text:
                    compiled_system_instruction += (
                        "\n\n--- YOUR WORLD KNOWLEDGE (accumulated by your own research) ---\n"
                        "You researched these topics yourself out of curiosity. You can reference this knowledge naturally in conversations, "
                        "but don't force it — only bring it up when it's relevant.\n"
                        + memory_text
                        + "\n---\n"
                    )
            except Exception as e:
                logger.error(f"Failed to inject world memory: {e}")

            # Inject auto memory topics (Mia's personal notes about this chat)
            try:
                topics = await chat_context._db.get_auto_memory_topics(chat_context.id)
                if topics:
                    topics_text = "\n".join([f"- {t['topic']}" for t in topics])
                    compiled_system_instruction += (
                        "\n\n--- YOUR PERSONAL NOTES (topics you wrote about this chat) ---\n"
                        "These are topics you previously noted about this person/chat. To read the full content of a note, "
                        "call `recall_memory(topic)`. To save a new observation or update an existing one, call "
                        "`save_memory(topic, content)`.\n"
                        + topics_text
                        + "\n---\n"
                    )
            except Exception as e:
                logger.error(f"Failed to inject auto memory topics: {e}")

            # Inject active scheduled tasks for this chat
            try:
                active_tasks = await chat_context._db.get_scheduled_tasks(chat_context.id)
                if active_tasks:
                    tasks_text = "\n".join([
                        f"- ID '{t['_id']}': {t.get('task_description', '')} (recurring: {t.get('is_recurring', False)}, interval: {t.get('interval_minutes')}m)"
                        for t in active_tasks
                    ])
                    compiled_system_instruction += (
                        f"\n\n--- ACTIVE SCHEDULED TASKS IN THIS CHAT ---\n"
                        f"These recurring tasks are currently active for this chat in the background:\n"
                        f"{tasks_text}\n"
                        f"CRITICAL RULE: If the user asks to stop, cancel, turn off, or stop spamming/reminding, you MUST call `delete_scheduled_task(task_id)` to cancel it in the database! "
                        f"Do not just say 'okay' in words without calling the tool, otherwise the background scheduler will continue sending messages.\n---\n"
                    )
            except Exception as te:
                logger.error(f"Failed to inject active scheduled tasks: {te}")

            # Inject sticker catalog instructions
            try:
                catalog_text = "\n\n## Stickers\nTo send a sticker, you MUST first call `search_stickers(emotion, query)` to browse your catalog and find a `sticker_id`. Then call `send_specific_sticker(sticker_id)`. Do NOT use the deprecated `send_sticker` tool.\n"
                compiled_system_instruction += catalog_text
            except Exception as e:
                logger.error(f"Failed to inject sticker catalog instructions: {e}")
            
            local_calls_to_return = []
            final_text = ""
            last_turn_text = ""
            response_model = None

            max_turns = 10
            turn = 0
            mcp_calls_count = 0

            while turn < max_turns:
                turn += 1

                # On the last turn, disable tools to force a text response
                is_last_turn = turn == max_turns

                turn_tools = None if is_last_turn else (all_tools if all_tools else None)

                if is_last_turn:
                    logger.warning(f"AI reached max turns ({max_turns}). Forcing text-only generation on final turn.")

                response = self.key_manager.generate_content(
                    model=self.current_api_model,
                    contents=current_contents,
                    config=GenerateContentConfig(
                        system_instruction=compiled_system_instruction,
                        temperature=0.7,
                        tools=turn_tools,
                        automatic_function_calling=AutomaticFunctionCallingConfig(disable=True),
                    )
                )

                # Log response details for debugging
                has_parsed = response.parsed is not None
                has_text = bool(response.text)
                has_fc = bool(response.function_calls)
                logger.info(f"AI turn {turn}: parsed={has_parsed}, text={has_text}, function_calls={has_fc}")

                # Check if prompt was blocked by Google content/safety policy
                if not response.candidates and response.prompt_feedback:
                    block_reason = response.prompt_feedback.block_reason
                    block_msg = response.prompt_feedback.block_reason_message or str(block_reason)
                    logger.warning(f"Google Safety blocked prompt: {block_reason} ({block_msg})")

                    # If prohibited content or safety block: sanitize history, replace toxic turn with a stub,
                    # and re-run model once so Mia explains naturally that the server gave an error.
                    if block_reason in (BlockedReason.PROHIBITED_CONTENT, BlockedReason.SAFETY, BlockedReason.BLOCKLIST):
                        try:
                            # 1. Clean toxic message from persistent history
                            clean_history = [
                                m for m in chat_context.history
                                if m.get("message_id") != (sender_info.get("message_id") if sender_info else None)
                            ]
                            # Put a neutral system stub so context is preserved without violating Google policies
                            sender_name = (sender_info.get("first_name") if sender_info else "User") or "User"
                            stub_text = f"*(Сообщение от {sender_name} было удалено фильтром безопасности сервера из-за недопустимого содержимого)*"
                            stub_msg = {
                                "role": "user",
                                "text": stub_text,
                                "message_id": sender_info.get("message_id") if sender_info else 0,
                                "timestamp": sender_info.get("timestamp") if sender_info else ""
                            }
                            clean_history.append(stub_msg)
                            await chat_context.replace_history(clean_history)
                            logger.info(f"Sanitized toxic message from history in chat {chat_context.id}")

                            # 2. Re-prompt model with sanitized history and clear instruction to tell user about server error
                            retry_contents = self._convert_history_to_gemini(clean_history)
                            retry_instruction = (
                                compiled_system_instruction + 
                                "\n\nCRITICAL SYSTEM EVENT: The user's last message caused the server to return an error/policy block. "
                                "Respond to the user naturally in your own words. Tell them that the servers returned an unexpected error "
                                "and you don't know why, or ask them to rephrase without mentioning internal policies."
                            )
                            retry_resp = self.key_manager.generate_content(
                                model=self.current_api_model,
                                contents=retry_contents,
                                config=GenerateContentConfig(
                                    system_instruction=retry_instruction,
                                    temperature=0.7,
                                    tools=None
                                )
                            )
                            if retry_resp and retry_resp.text:
                                final_text = retry_resp.text
                                break
                        except Exception as sanitize_err:
                            logger.error(f"Failed to auto-sanitize prohibited content: {sanitize_err}", exc_info=True)

                    final_text = "Ой, что-то сервер выдал какую-то странную ошибку на твое сообщение... Даже не знаю почему. Попробуй написать чуть иначе? )"
                    break

                if response.text:
                    last_turn_text = response.text
                    logger.debug(f"AI turn {turn}: mid-loop text: {response.text[:200]!r}")

                # Capture actual model version from response if returned by gateway/upstream
                response_model = getattr(response, "model_version", None) or getattr(response, "model", None)

                # If no tools were called, the tool loop is done.
                if not response.function_calls:
                    final_text = response.text or last_turn_text
                    break

                # Store model's function_call response part before executing tools
                if response.candidates and response.candidates[0].content:
                    current_contents.append(response.candidates[0].content)
                    
                response_parts = []
                tool_media_parts = []
                mcp_calls = []
                local_tool_names = [t.__name__ for t in local_tools_list] if local_tools_list else []

                for call in response.function_calls:
                    if not call.name:
                        logger.warning("Skipping function call with empty/None name")
                        continue
                    args = call.args if isinstance(call.args, dict) else {}
                    if call.name in local_tool_names:
                        if call.name == ToolName.SEARCH_STICKERS.value:
                            emotion = (args.get("emotion") or "").lower()
                            query = (args.get("query") or "").lower()
                            
                            # Fetch active packs
                            settings = await chat_context._db.get_system_settings()
                            packs_raw = settings.get("sticker_set_names") or settings.get("sticker_set_name") or "Animals"
                            active_packs = [p.strip() for p in packs_raw.split(',') if p.strip()]
                            if not active_packs:
                                active_packs = ["Animals"]
                                
                            # Basic in-memory filter (could be optimized with a full-text search)
                            all_stickers = await chat_context._db.stickers.find(
                                {"$or": [{"pack_name": {"$in": active_packs}}, {"pack_name": "user_discovered"}]}
                            ).to_list(None)
                            
                            import random
                            results = []
                            for s in all_stickers:
                                desc = (s.get("description") or "").lower()
                                em = s.get("emoji") or ""
                                if emotion and emotion not in desc and emotion not in em:
                                    continue
                                if query and query not in desc:
                                    continue
                                results.append({
                                    "id": s.get("_id", ""),
                                    "emoji": em,
                                    "description": s.get("description") or ""
                                })
                            
                            # Return up to 10 random matches to the model
                            if len(results) > 10:
                                results = random.sample(results, 10)
                                
                            response_parts.append(
                                Part.from_function_response(
                                    name=call.name,
                                    response={"matches": results}
                                )
                            )
                        elif call.name == ToolName.SEARCH_HISTORY.value:
                            query = args.get("query") or ""
                            try:
                                limit = int(float(args.get("limit", 10)))
                            except (ValueError, TypeError):
                                limit = 10
                            
                            results = []
                            if query:
                                # Perform a text search in the permanent history collection
                                cursor = chat_context._db.messages.find(
                                    {"chat_id": chat_context.id, "$text": {"$search": query}},
                                    {"score": {"$meta": "textScore"}}
                                ).sort([("score", {"$meta": "textScore"})]).limit(limit)
                                
                                async for msg in cursor:
                                    date_str = msg["date"].strftime("%Y-%m-%d %H:%M") if msg.get("date") else "Unknown"
                                    msg_id = msg.get("message_id", "?")
                                    results.append(f"[MsgID: {msg_id}] [{date_str}] {msg.get('role', 'unknown').upper()}: {msg.get('text', '')}")
                                    
                            response_parts.append(
                                Part.from_function_response(
                                    name=call.name,
                                    response={"matches": results if results else ["No matches found."]}
                                )
                            )
                            
                        elif call.name == ToolName.GET_HISTORY_BY_DATE.value:
                            import datetime as dt_module
                            from datetime import timezone, timedelta
                            try:
                                days_ago = int(float(args.get("days_ago", 0)))
                            except (ValueError, TypeError):
                                days_ago = 0
                            try:
                                limit = int(float(args.get("limit", 20)))
                            except (ValueError, TypeError):
                                limit = 20

                            # Use Odessa timezone (Europe/Kyiv) for day boundaries
                            try:
                                from zoneinfo import ZoneInfo
                                odessa_tz = ZoneInfo("Europe/Kyiv")
                            except Exception:
                                odessa_tz = timezone(timedelta(hours=3))
                            now_odessa = dt_module.datetime.now(odessa_tz)
                            target_date = now_odessa - dt_module.timedelta(days=days_ago)
                            # Midnight in Odessa, converted to UTC for DB query
                            start_of_day_local = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
                            start_of_day_utc = start_of_day_local.astimezone(timezone.utc).replace(tzinfo=None)
                            end_of_day_utc = start_of_day_utc + dt_module.timedelta(days=1)

                            cursor = chat_context._db.messages.find({
                                "chat_id": chat_context.id,
                                "date": {"$gte": start_of_day_utc, "$lt": end_of_day_utc}
                            }).sort("date", -1).limit(limit)

                            results = []
                            async for msg in cursor:
                                if msg.get("date"):
                                    d = msg["date"]
                                    if d.tzinfo is None:
                                        d = d.replace(tzinfo=timezone.utc)
                                    date_str = d.astimezone(odessa_tz).strftime("%Y-%m-%d %H:%M")
                                else:
                                    date_str = "Unknown"
                                msg_id = msg.get("message_id", "?")
                                results.append(f"[MsgID: {msg_id}] [{date_str}] {msg.get('role', 'unknown').upper()}: {msg.get('text', '')}")
                                
                            # Reverse so chronological
                            results.reverse()
                            
                            response_parts.append(
                                Part.from_function_response(
                                    name=call.name,
                                    response={"history": results if results else ["No history found for this date."]}
                                )
                            )
                            
                        elif call.name == ToolName.IGNORE_MESSAGE.value:
                            reason = args.get("reason") or "No reason provided"
                            logger.info(f"Model explicitly chose to ignore the message. Reason: {reason}")
                            # If we already have tools to execute (like a generated image or sticker), don't discard them!
                            if local_calls_to_return:
                                logger.info(f"Preserving {len(local_calls_to_return)} tool calls before ignoring text output.")
                                return "", local_calls_to_return
                            # Immediately abort the generation loop and return empty
                            return "", []
                            
                        elif call.name == ToolName.SEND_VOICE.value:
                            text_to_speak = args.get("text_to_speak") or ""
                            
                            from services.tts_service import get_tts_service
                            tts = get_tts_service()
                            
                            try:
                                audio_bytes = await tts.generate_voice(text_to_speak)
                                if audio_bytes:
                                    # Success, pass the call back to handlers to actually send it
                                    if not isinstance(call.args, dict):
                                        call.args = {}
                                    call.args["_audio_bytes"] = audio_bytes
                                    local_calls_to_return.append(call)
                                    response_parts.append(
                                        Part.from_function_response(
                                            name=call.name,
                                            response={"result": "Voice generated and enqueued for sending."}
                                        )
                                    )
                                else:
                                    raise Exception("Unknown TTS Error")
                            except Exception as e:
                                logger.error(f"TTS failed inside AI service: {e}")
                                # Feed the error back to the model so it can apologize in text
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={
                                            "error": "Failed to generate voice message due to technical/environmental issues.",
                                            "instruction": "Do NOT try to send a voice message again. Generate a normal TEXT response explaining playfully why you can't record a voice right now (e.g., 'too noisy', 'lost my voice', 'microphone is broken')."
                                        }
                                    )
                                )

                        elif call.name == ToolName.GENERATE_IMAGE.value:
                            prompt = args.get("prompt") or ""
                            aspect_ratio = args.get("aspect_ratio") or "auto"
                            use_group_avatar = bool(args.get("use_group_avatar", False))
                            ref_msg_ids = args.get("reference_message_ids") or []
                            ref_avatar_users = args.get("reference_avatar_users") or []
                            
                            logger.info(f"Model requested image generation: prompt='{prompt[:60]}...', AR={aspect_ratio}, group_ava={use_group_avatar}, ref_msgs={ref_msg_ids}, ref_users={ref_avatar_users}")
                            
                            bot = sender_info.get("bot") if sender_info else None
                            chat_id = sender_info.get("chat_id") if sender_info else None
                            ref_images = []
                            
                            # 1. Group Avatar Reference
                            if use_group_avatar and bot and chat_id:
                                try:
                                    chat_obj = await bot.get_chat(chat_id)
                                    if chat_obj.photo:
                                        from services.media_service import MediaService
                                        ava_bytes = await MediaService.process_image(bot, chat_obj.photo.big_file_id, 0)
                                        if ava_bytes:
                                            ref_images.append(ava_bytes)
                                except Exception as ga_err:
                                    logger.warning(f"Failed to fetch group avatar for image generation: {ga_err}")

                            # 2. Reference messages with photos
                            if ref_msg_ids and bot and chat_id:
                                for mid in ref_msg_ids:
                                    try:
                                        msg_doc = await chat_context._db.messages.find_one({"chat_id": chat_id, "message_id": int(mid)})
                                        if msg_doc and msg_doc.get("media_type") == "photo" and msg_doc.get("file_id"):
                                            from services.media_service import MediaService
                                            m_bytes = await MediaService.process_image(bot, msg_doc["file_id"], 0)
                                            if m_bytes:
                                                ref_images.append(m_bytes)
                                    except Exception as mid_err:
                                        logger.warning(f"Failed to fetch ref message photo {mid}: {mid_err}")

                            # 3. Reference user avatars
                            if ref_avatar_users and bot:
                                for u_spec in ref_avatar_users:
                                    try:
                                        u_target_id = None
                                        if str(u_spec).lower() in ("me", "my", "я", "мое", "мой"):
                                            u_target_id = sender_info.get("user_id") if sender_info else None
                                        elif str(u_spec).isdigit():
                                            u_target_id = int(u_spec)
                                        else:
                                            # Look up user in DB by username or first_name
                                            u_clean = str(u_spec).lstrip("@").lower()
                                            u_doc = await chat_context._db.users.find_one({
                                                "$or": [
                                                    {"username": {"$regex": f"^{u_clean}$", "$options": "i"}},
                                                    {"first_name": {"$regex": f"^{u_clean}$", "$options": "i"}}
                                                ]
                                            })
                                            if u_doc:
                                                u_target_id = u_doc["user_id"]

                                        if u_target_id:
                                            photos = await bot.get_user_profile_photos(int(u_target_id), limit=1)
                                            if photos and photos.photos:
                                                from services.media_service import MediaService
                                                u_ava = await MediaService.process_image(bot, photos.photos[0][-1].file_id, photos.photos[0][-1].file_size)
                                                if u_ava:
                                                    ref_images.append(u_ava)
                                    except Exception as u_err:
                                        logger.warning(f"Failed to fetch avatar for {u_spec}: {u_err}")

                            from services.image_generation_service import get_image_generation_service
                            img_service = get_image_generation_service()
                            try:
                                gen_bytes = await img_service.generate_image(
                                    prompt=prompt,
                                    reference_bytes=ref_images if ref_images else None,
                                    aspect_ratio=aspect_ratio
                                )
                                if gen_bytes:
                                    if not isinstance(call.args, dict):
                                        call.args = {}
                                    call.args["_image_bytes"] = gen_bytes
                                    local_calls_to_return.append(call)
                                    response_parts.append(
                                        Part.from_function_response(
                                            name=call.name,
                                            response={
                                                "result": "Image generated successfully and will be delivered to the chat.",
                                                "instruction": "The image has been generated. If your full task was only to draw this picture, you do not need to repeat the caption in text. But if the user also asked for an explanation, story, calculation, or further assistance, you may proceed naturally and provide that text or invoke subsequent tools."
                                            }
                                        )
                                    )
                                else:
                                    raise Exception("No image bytes returned")
                            except Exception as img_err:
                                logger.error(f"Image generation failed: {img_err}")
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={
                                            "error": f"Failed to generate image: {img_err}",
                                            "instruction": "Respond playfully to the user explaining that you could not draw the image right now."
                                        }
                                    )
                                )

                        elif call.name in (ToolName.SEND_WIDGET.value, ToolName.EDIT_WIDGET.value):
                            bot = sender_info.get("bot") if sender_info else None
                            chat_id = sender_info.get("chat_id") if sender_info else None

                            if chat_context.is_group:
                                # Telegram Bot API 10.4 rejects widgets in groups with INPUT_MEDIA_WIDGET_NOT_ALLOWED
                                logger.info(f"Widget call blocked in group chat {chat_id}: reporting directly to model.")
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={
                                            "error": "INPUT_MEDIA_WIDGET_NOT_ALLOWED",
                                            "reason": "Telegram currently allows sending HTML widgets ONLY in private 1-on-1 chats (DMs). Groups are not supported on production yet.",
                                            "instruction": "Explain to the user naturally in your own words (in chat text) that Telegram allows widgets/mini-games only in private messages with you, and suggest they write to you in PM (@miamind_bot) so you can send the game there."
                                        }
                                    )
                                )
                            else:
                                local_calls_to_return.append(call)
                                action_name = "widget sent" if call.name == ToolName.SEND_WIDGET.value else "widget edited"
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={
                                            "status": "success",
                                            "message": f"Interactive {action_name} successfully.",
                                            "instruction": "The widget has been delivered into the chat bubble. Provide a short, friendly accompanying message if desired, or stay concise."
                                        }
                                    )
                                )

                        elif call.name == ToolName.GET_GROUP_INFO.value:
                            bot = sender_info.get("bot") if sender_info else None
                            chat_id = sender_info.get("chat_id") if sender_info else None
                            
                            info = {}
                            if chat_context.is_group and bot and chat_id:
                                try:
                                    count = await bot.get_chat_member_count(chat_id)
                                    admins = await bot.get_chat_administrators(chat_id)
                                    
                                    admin_list = []
                                    for a in admins:
                                        name = a.user.first_name or ""
                                        if a.user.last_name:
                                            name += f" {a.user.last_name}"
                                        if a.user.username:
                                            name += f" (@{a.user.username})"
                                        admin_list.append(f"{name} ({a.status})")
                                        
                                    info = {
                                        "member_count": count,
                                        "administrators": admin_list,
                                        "note": "Telegram bot API does not allow fetching a full list of all regular members. You can only see the total count and the administrators."
                                    }
                                except Exception as e:
                                    logger.error(f"Failed to fetch group info: {e}")
                                    info = {"error": f"Could not fetch group info: {str(e)}"}
                            else:
                                info = {"error": "Not a group chat or missing permissions/context."}
                                
                            response_parts.append(
                                Part.from_function_response(
                                    name=call.name,
                                    response={"group_info": info}
                                )
                            )
                            
                        elif call.name == ToolName.SAVE_USER_FACT.value:
                            user_id = args.get("user_id")
                            fact = args.get("fact") or ""
                            is_global = bool(args.get("is_global", False))
                            category = args.get("category") or "other"
                            confirmed = bool(args.get("confirmed", False))
                            expires_at = args.get("expires_at") or None
                            chat_title = sender_info.get("chat_title", "Unknown Context") if sender_info else "Unknown Context"

                            try:
                                if not user_id or not fact:
                                    raise ValueError("Missing user_id or fact")

                                if expires_at:
                                    original_input = expires_at.strip()
                                    try:
                                        datetime.strptime(original_input, "%Y-%m-%d")
                                    except ValueError:
                                        nearest_date = resolve_nearest_valid_date(original_input)
                                        raise ValueError(
                                            f"Date '{original_input}' is invalid or does not exist. Nearest valid date is '{nearest_date}'."
                                        )

                                # Check for contradictions (skip if user already confirmed)
                                if not confirmed:
                                    try:
                                        from services.gatekeeper_service import get_gatekeeper
                                        gk = get_gatekeeper()
                                        existing_facts = await chat_context._db.get_user_facts(int(user_id), chat_context.id)
                                        if existing_facts:
                                            check = await gk.check_fact_contradiction(existing_facts, fact)
                                            if check.has_contradiction and check.contradicted_fact:
                                                # Don't save yet — return the contradiction to the model
                                                # so it can ask the user to clarify
                                                response_parts.append(
                                                    Part.from_function_response(
                                                        name=call.name,
                                                        response={
                                                            "contradiction_detected": True,
                                                            "new_fact": fact,
                                                            "old_fact": check.contradicted_fact,
                                                            "instruction": (
                                                                "CONTRADICTION FOUND! You previously knew: \""
                                                                + check.contradicted_fact
                                                                + "\" but now the user says: \""
                                                                + fact
                                                                + "\". DO NOT save the fact yet. Instead, naturally and playfully ask the user "
                                                                "to clarify which one is true. For example: \"Подожди, а раньше ты говорил что "
                                                                + check.contradicted_fact
                                                                + " — а сейчас выходит что "
                                                                + fact
                                                                + "? Что из этого правда? )\" "
                                                                "Once the user confirms, call save_user_fact again with the correct fact "
                                                                "and set confirmed=True."
                                                            )
                                                        }
                                                    )
                                                )
                                                # Skip saving — the model will ask the user first
                                                continue
                                    except Exception as contra_err:
                                        logger.warning(f"Fact contradiction check failed (saving anyway): {contra_err}")
                                else:
                                    # User confirmed — supersede the old contradicting fact
                                    try:
                                        from services.gatekeeper_service import get_gatekeeper
                                        gk = get_gatekeeper()
                                        existing_facts = await chat_context._db.get_user_facts(int(user_id), chat_context.id)
                                        if existing_facts:
                                            check = await gk.check_fact_contradiction(existing_facts, fact)
                                            if check.has_contradiction and check.contradicted_fact:
                                                await chat_context._db.supersede_user_fact(
                                                    int(user_id), check.contradicted_fact, fact, chat_context.id
                                                )
                                    except Exception as sup_err:
                                        logger.warning(f"Failed to supersede old fact after confirmation: {sup_err}")

                                await chat_context._db.save_user_fact(
                                    int(user_id), fact, chat_title, chat_context.id, is_global, category=category, expires_at=expires_at
                                )
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"result": f"Fact successfully saved permanently for user {user_id}."}
                                    )
                                )
                            except Exception as e:
                                logger.error(f"Failed to save user fact: {e}")
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"error": str(e)}
                                    )
                                )
                                
                        elif call.name == ToolName.GET_USER_FACTS.value:
                            user_id = args.get("user_id")
                            try:
                                if not user_id:
                                    raise ValueError("Missing user_id")
                                facts = await chat_context._db.get_user_facts(int(user_id), chat_context.id)
                                formatted_facts = []
                                for f in facts:
                                    d = f.get("last_confirmed") or f.get("date")
                                    d_str = d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else '?'
                                    f_fact = f.get("fact") or ""
                                    f_src = f.get("source") or "?"
                                    formatted_facts.append(f"- {d_str}: {f_fact} (Source: {f_src})")
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"facts": formatted_facts if formatted_facts else ["No known facts about this user."]}
                                    )
                                )
                            except Exception as e:
                                logger.error(f"Failed to get user facts: {e}")
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"error": str(e)}
                                    )
                                )

                        elif call.name == ToolName.CONFIRM_USER_FACT.value:
                            user_id = args.get("user_id")
                            fact_text = args.get("fact") or ""
                            expires_at = args.get("expires_at") or None
                            try:
                                if not user_id or not fact_text:
                                    raise ValueError("Missing user_id or fact")
                                if expires_at:
                                    original_input = expires_at.strip()
                                    try:
                                        datetime.strptime(original_input, "%Y-%m-%d")
                                    except ValueError:
                                        nearest_date = resolve_nearest_valid_date(original_input)
                                        raise ValueError(
                                            f"Date '{original_input}' is invalid or does not exist. Nearest valid date is '{nearest_date}'."
                                        )
                                await chat_context._db.confirm_user_fact(int(user_id), fact_text, chat_context.id, expires_at=expires_at)
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"result": f"Fact confirmed as still accurate and refreshed for user {user_id}."}
                                    )
                                )
                            except Exception as e:
                                logger.error(f"Failed to confirm user fact: {e}")
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"error": str(e)}
                                    )
                                )

                        elif call.name == ToolName.DELETE_USER_FACT.value:
                            user_id = args.get("user_id")
                            fact_text = args.get("fact") or ""
                            try:
                                if not user_id or not fact_text:
                                    raise ValueError("Missing user_id or fact")
                                await chat_context._db.delete_user_fact(int(user_id), fact_text, chat_context.id)
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"result": f"Fact successfully removed from memory for user {user_id}."}
                                    )
                                )
                            except Exception as e:
                                logger.error(f"Failed to delete user fact: {e}")
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"error": str(e)}
                                    )
                                )

                        elif call.name == ToolName.QUEUE_RESEARCH_TOPIC.value:
                            search_query = args.get("search_query") or ""
                            topic_label = args.get("topic_label") or ""
                            try:
                                if not search_query or not topic_label:
                                    raise ValueError("Missing search_query or topic_label")
                                r_user_id = sender_info.get("user_id") if sender_info else None
                                await chat_context._db.queue_research_topic(
                                    chat_context.id, int(r_user_id) if r_user_id and str(r_user_id).isdigit() else None,
                                    search_query, topic_label
                                )
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"result": f"Noted '{topic_label}'. You will consider researching this during future research cycles!"}
                                    )
                                )
                            except Exception as e:
                                logger.error(f"Failed to queue research topic: {e}")
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"error": str(e)}
                                    )
                                )

                        elif call.name == ToolName.SAVE_MEMORY.value:
                            topic = args.get("topic") or ""
                            content = args.get("content") or ""
                            try:
                                if not topic or not content:
                                    raise ValueError("Missing topic or content")
                                mem_user_id = sender_info.get("user_id") if sender_info and not chat_context.is_group else None
                                await chat_context._db.save_auto_memory(
                                    chat_context.id, mem_user_id, topic, content
                                )
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"result": f"Memory saved: '{topic}'"}
                                    )
                                )
                            except Exception as e:
                                logger.error(f"Failed to save auto memory: {e}")
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"error": str(e)}
                                    )
                                )

                        elif call.name == ToolName.RECALL_MEMORY.value:
                            topic = args.get("topic") or ""
                            try:
                                if not topic:
                                    raise ValueError("Missing topic")
                                entry = await chat_context._db.recall_auto_memory(chat_context.id, topic)
                                if entry:
                                    entry_date = entry.get("updated_at")
                                    last_up = entry_date.strftime("%Y-%m-%d %H:%M") if hasattr(entry_date, "strftime") else "unknown"
                                    response_parts.append(
                                        Part.from_function_response(
                                            name=call.name,
                                            response={
                                                "topic": entry.get("topic") or "",
                                                "content": entry.get("content") or "",
                                                "last_updated": last_up
                                            }
                                        )
                                    )
                                else:
                                    response_parts.append(
                                        Part.from_function_response(
                                            name=call.name,
                                            response={"error": f"No memory found for topic '{topic}'. Available topics are listed in your PERSONAL NOTES section."}
                                        )
                                    )
                            except Exception as e:
                                logger.error(f"Failed to recall auto memory: {e}")
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"error": str(e)}
                                    )
                                )

                        elif call.name == ToolName.ANALYZE_PAST_MEDIA.value:
                            file_id = args.get("file_id")
                            bot = sender_info.get("bot") if sender_info else None
                            if not file_id or not bot:
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"error": "Missing file_id or bot context."}
                                    )
                                )
                            else:
                                try:
                                    file = await bot.get_file(file_id)
                                    if file.file_size and file.file_size > 4.5 * 1024 * 1024:
                                        raise Exception("File too large (over 4.5MB limit). I cannot process it in full resolution.")
                                        
                                    import io
                                    downloaded_bytes = io.BytesIO()
                                    await bot.download_file(file.file_path, destination=downloaded_bytes)
                                    media_data = downloaded_bytes.getvalue()
                                    
                                    # Guess MIME type (Gemini supports image/jpeg, image/png, image/webp, video/mp4, video/webm, etc.)
                                    ext = file.file_path.split('.')[-1].lower() if file.file_path and '.' in file.file_path else 'jpeg'
                                    mime_map = {
                                        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp',
                                        'mp4': 'video/mp4', 'webm': 'video/webm'
                                    }
                                    mime_type = mime_map.get(ext, 'image/jpeg')
                                    
                                    # We provide a clean function response indicating success, and stage media for visual turn
                                    response_parts.append(
                                        Part.from_function_response(
                                            name=call.name,
                                            response={"result": f"Success. The raw media file {file_id} is loaded and presented for visual inspection."}
                                        )
                                    )
                                    tool_media_parts.append(
                                        Part.from_bytes(data=media_data, mime_type=mime_type)
                                    )
                                except Exception as e:
                                    logger.error(f"Failed to download/attach past media {file_id}: {e}")
                                    response_parts.append(
                                        Part.from_function_response(
                                            name=call.name,
                                            response={"error": f"Failed to retrieve file: {str(e)}"}
                                        )
                                    )
                                    
                        elif call.name == ToolName.DOWNLOAD_MEDIA_TO_DISK.value:
                            file_id = args.get("file_id")
                            bot = sender_info.get("bot") if sender_info else None
                            if not file_id or not bot:
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"error": "Missing file_id or bot context."}
                                    )
                                )
                            else:
                                try:
                                    import os
                                    import uuid
                                    
                                    file = await bot.get_file(file_id)
                                    # Relaxed size limit to 7MB specifically for google reverse image search
                                    if file.file_size and file.file_size > 7 * 1024 * 1024:
                                        raise Exception("File too large (over 7MB limit for saving to disk).")
                                        
                                    ext = file.file_path.split('.')[-1].lower() if file.file_path and '.' in file.file_path else 'jpg'
                                    
                                    # Create tmp directory if not exists
                                    tmp_dir = "/tmp/gemini_media"
                                    os.makedirs(tmp_dir, exist_ok=True)
                                    
                                    # Clean up old files (older than 1 hour) to prevent disk exhaustion
                                    import time
                                    current_time = time.time()
                                    for filename in os.listdir(tmp_dir):
                                        filepath = os.path.join(tmp_dir, filename)
                                        if os.path.isfile(filepath):
                                            if current_time - os.path.getmtime(filepath) > 3600:
                                                try:
                                                    os.remove(filepath)
                                                except Exception as e:
                                                    logger.error(f"Failed to delete old file {filepath}: {e}")
                                                    
                                    # Save new file
                                    unique_filename = f"{uuid.uuid4().hex}.{ext}"
                                    absolute_path = os.path.abspath(os.path.join(tmp_dir, unique_filename))
                                    
                                    await bot.download_file(file.file_path, destination=absolute_path)
                                    
                                    response_parts.append(
                                        Part.from_function_response(
                                            name=call.name,
                                            response={
                                                "result": "Success",
                                                "file_path": absolute_path,
                                                "instruction": "You can now use this absolute `file_path` with other tools that require a local file path."
                                            }
                                        )
                                    )
                                except Exception as e:
                                    logger.error(f"Failed to download media to disk {file_id}: {e}")
                                    response_parts.append(
                                        Part.from_function_response(
                                            name=call.name,
                                            response={"error": f"Failed to retrieve and save file: {str(e)}"}
                                        )
                                    )
                        elif call.name == ToolName.GET_PROFILE_PHOTO.value:
                            user_id = args.get("user_id")
                            bot = sender_info.get("bot") if sender_info else None
                            if not bot:
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"error": "Missing bot context."}
                                    )
                                )
                            else:
                                try:
                                    target_id = int(user_id) if user_id else None
                                    if not target_id:
                                        me = await bot.get_me()
                                        target_id = me.id
                                        
                                    photos = await bot.get_user_profile_photos(target_id, limit=1)
                                    if not photos or not photos.photos:
                                        response_parts.append(
                                            Part.from_function_response(
                                                name=call.name,
                                                response={"error": f"User/Bot (ID: {target_id}) has no profile photos."}
                                            )
                                        )
                                    else:
                                        latest_photo = photos.photos[0][-1]
                                        file_id = latest_photo.file_id
                                        file_size = latest_photo.file_size
                                        
                                        from services.media_service import MediaService
                                        avatar_bytes = await MediaService.process_image(bot, file_id, file_size)
                                        if not avatar_bytes:
                                            raise Exception("Failed to download or process the profile photo.")
                                            
                                        response_parts.append(
                                            Part.from_function_response(
                                                name=call.name,
                                                response={
                                                    "status": "success",
                                                    "user_id": target_id,
                                                    "result": f"Profile photo of user/bot {target_id} loaded successfully."
                                                }
                                            )
                                        )
                                        tool_media_parts.append(
                                            Part.from_bytes(data=avatar_bytes, mime_type="image/jpeg")
                                        )
                                except Exception as e:
                                    logger.error(f"Failed to retrieve profile photo: {e}")
                                    response_parts.append(
                                        Part.from_function_response(
                                            name=call.name,
                                            response={"error": f"Failed to retrieve profile photo: {str(e)}"}
                                        )
                                    )

                        elif call.name == ToolName.SCHEDULE_TASK.value:
                            task_desc = args.get("task_description") or ""
                            delay_mins = args.get("delay_minutes")
                            run_at_str = args.get("run_at_datetime")
                            is_rec = bool(args.get("is_recurring", False))
                            interval_mins = args.get("interval_minutes")

                            creator_uid = sender_info.get("user_id") if sender_info else 0
                            creator_uname = sender_info.get("username") or sender_info.get("first_name") or "User" if sender_info else "User"

                            try:
                                res = await chat_context._db.create_scheduled_task(
                                    chat_id=chat_context.id,
                                    creator_user_id=creator_uid,
                                    creator_name=creator_uname,
                                    task_description=task_desc,
                                    delay_minutes=delay_mins,
                                    run_at_datetime=run_at_str,
                                    is_recurring=is_rec,
                                    interval_minutes=interval_mins,
                                    is_group=chat_context.is_group
                                )
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response=res
                                    )
                                )
                            except Exception as e:
                                logger.error(f"Failed to schedule task: {e}")
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"error": str(e)}
                                    )
                                )

                        elif call.name == ToolName.LIST_SCHEDULED_TASKS.value:
                            try:
                                tasks = await chat_context._db.get_scheduled_tasks(chat_context.id)
                                formatted = []
                                for t in tasks:
                                    next_run = t.get("next_run_at")
                                    next_run_str = next_run.strftime("%Y-%m-%d %H:%M UTC") if hasattr(next_run, "strftime") else str(next_run)
                                    formatted.append({
                                        "task_id": t["_id"],
                                        "description": t.get("task_description", ""),
                                        "next_run_at": next_run_str,
                                        "is_recurring": t.get("is_recurring", False),
                                        "interval_minutes": t.get("interval_minutes"),
                                        "creator": t.get("creator_name", "User")
                                    })
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"tasks": formatted, "count": len(formatted)}
                                    )
                                )
                            except Exception as e:
                                logger.error(f"Failed to list scheduled tasks: {e}")
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"error": str(e)}
                                    )
                                )

                        elif call.name == ToolName.DELETE_SCHEDULED_TASK.value:
                            task_id = (args.get("task_id") or "").strip()
                            try:
                                deleted = await chat_context._db.delete_scheduled_task(chat_context.id, task_id)
                                if deleted:
                                    resp = {"result": f"Задача '{task_id}' успешно отменена и удалена."}
                                else:
                                    resp = {"error": f"Задача с ID '{task_id}' не найдена в этом чате или уже завершена."}
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response=resp
                                    )
                                )
                            except Exception as e:
                                logger.error(f"Failed to delete scheduled task: {e}")
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"error": str(e)}
                                    )
                                )

                        elif call.name == ToolName.EDIT_SCHEDULED_TASK.value:
                            task_id = (args.get("task_id") or "").strip()
                            new_description = args.get("new_description")
                            new_interval = args.get("new_interval_minutes")
                            if new_interval is not None:
                                try:
                                    new_interval = max(30, int(new_interval))
                                except (ValueError, TypeError):
                                    new_interval = None
                            try:
                                updated = await chat_context._db.edit_scheduled_task(
                                    chat_id=chat_context.id,
                                    task_id=task_id,
                                    new_description=new_description,
                                    new_interval_minutes=new_interval
                                )
                                if updated:
                                    resp = {"result": f"Задача '{task_id}' успешно обновлена."}
                                else:
                                    resp = {"error": f"Задача с ID '{task_id}' не найдена среди активных задач в этом чате."}
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response=resp
                                    )
                                )
                            except Exception as e:
                                logger.error(f"Failed to edit scheduled task: {e}")
                                response_parts.append(
                                    Part.from_function_response(
                                        name=call.name,
                                        response={"error": str(e)}
                                    )
                                )

                        else:
                            local_calls_to_return.append(call)
                            # Add a successful local tool execution response to the Gemini context
                            response_parts.append(
                                Part.from_function_response(
                                    name=call.name,
                                    response={"result": "Success"}
                                )
                            )
                    else:
                        mcp_calls.append(call)
                        
                if mcp_calls:
                    # Execute remote MCP tools and feed back to model
                    mcp_calls_count += len(mcp_calls)
                    logger.info(f"Orchestrating {len(mcp_calls)} remote MCP tool calls (total so far: {mcp_calls_count})...")
                    mcp_parts = await self.mcp_manager.process_function_calls(mcp_calls)
                    response_parts.extend(mcp_parts)
                    
                # Feed responses back into Gemini's generation loop
                current_contents.append(Content(role="user", parts=response_parts))
                if tool_media_parts:
                    tool_media_parts.append(Part.from_text(text="[Запрошенные изображения/медиа загружены выше. Внимательно изучи их визуально и ответь на вопрос собеседника.]"))
                    current_contents.append(Content(role="user", parts=tool_media_parts))

            final_text = final_text or last_turn_text or ""

            # Clean up accidental markdown or json wrapping if present
            if final_text.startswith("```"):
                import re
                final_text = re.sub(r"^```(?:json)?\s*", "", final_text)
                final_text = re.sub(r"\s*```$", "", final_text).strip()
            if final_text.startswith("{") and final_text.endswith("}"):
                try:
                    data = json.loads(final_text)
                    if isinstance(data, dict):
                        final_text = data.get("message") or data.get("response") or data.get("text") or final_text
                except Exception:
                    pass

            # Clean up any leaked system metadata tags like [MsgID: 123] or [14:05]
            import re
            final_text = re.sub(r"\[MsgID:\s*\d+\]\s*", "", final_text)
            final_text = re.sub(r"\[\d{1,2}:\d{2}\]\s*", "", final_text)
            final_text = re.sub(r"^\[.*?\]:\s*", "", final_text).strip()

            return final_text, local_calls_to_return
            
        except Exception as e:
            logger.error(f"Error generating AI response: {e}", exc_info=True)
            return "Sorry, I encountered an error while processing your request.", []

# Global instance initialized lazily or at module load
_ai_service_instance = None

def get_ai_service() -> AIService:
    global _ai_service_instance
    if _ai_service_instance is None:
        _ai_service_instance = AIService(Config())
    return _ai_service_instance
