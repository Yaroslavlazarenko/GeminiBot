import logging
import os
from core.config import Config
from core.key_manager import GeminiKeyManager, get_key_manager
from google.genai.types import GenerateContentConfig, FunctionCall, Content, Tool, AutomaticFunctionCallingConfig, Part
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
            if reactions:
                meta_parts.append(f"[реакции: {' '.join(reactions)}]")

            if meta_parts:
                text = " ".join(meta_parts) + " " + text

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
                current_turn_parts.append({"text": text})
            if media_list:
                for media in media_list:
                    current_turn_parts.append({
                        "inline_data": {
                            "mime_type": media["mime_type"],
                            "data": media["data"]
                        }
                    })
                
            current_contents = gemini_history + [{"role": "user", "parts": current_turn_parts}]
            
            # Calculate Odessa local time (UTC+3)
            from datetime import datetime, timezone, timedelta
            odessa_tz = timezone(timedelta(hours=3))
            now_odessa = datetime.now(odessa_tz)
            time_str = now_odessa.strftime("%Y-%m-%d %H:%M:%S (%A)")

            # Prepare dynamic system instruction parts
            time_context = (
                f"\n\n--- DYNAMIC CONTEXT (SYSTEM TIME) ---\n"
                f"Current local time in Odessa, Ukraine (your city): {time_str}\n"
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
                    facts = await chat_context._db.get_user_facts(user_id) if user_id != "Unknown" else []
                    facts_str = "\n".join([f"- {f['date'].strftime('%Y-%m-%d')}: {f['fact']} (Source: {f['source']})" for f in facts]) if facts else "No known facts yet."
                except Exception as e:
                    logger.error(f"Failed to fetch user facts: {e}")
                    facts_str = "Error loading facts."

                sender_context = (
                    f"\n--- INTERLOCUTOR INFO ---\n"
                    f"User ID: {user_id}\n"
                    f"Name: {full_name}\n"
                    f"Username: {username_str}\n"
                    f"Telegram Language Setting: {lang}\n"
                    f"Visual description of their current Avatar (as seen by you, Mia): \"{avatar_desc}\"\n"
                    f"Known permanent facts about this user:\n{facts_str}\n"
                )

            tool_constraints = (
                f"\n--- TOOL USAGE & FORMATTING RULES ---\n"
                f"1. Never output text markers like \"(Голосовое сообщение):\", \"*(Голосовое сообщение)*:\", \"*(Отправляет стикер)*\", or similar mock actions in your text responses!\n"
                f"2. Never manually type \"[MsgID: 12345]\", \"[Name]:\", or timestamps in your text response. Tags like '[MsgID: 42] [14:05] [Alex]:' are internal system metadata indicating the message ID, time, and the speaker's name in group chats. Do NOT treat them as part of the user's message, and do NOT mimic them in your own replies. If you want to reply, use the `reply_to_message` tool silently.\n"
                f"3. If you want to send a voice message, you MUST call the `send_voice(text_to_speak)` tool. Do not simulate it in text.\n"
                f"4. If you want to send a sticker, you MUST call the `send_sticker(emotion)` tool. Do not write *(Отправляет стикер)* or descriptions of stickers in your text.\n"
                f"5. Proactively use `save_user_fact(user_id, fact)` to permanently memorize important details, preferences, or secrets the user shares with you. You can see their existing facts in the INTERLOCUTOR INFO.\n"
                f"6. Keep your text responses clean and natural, containing only what you would actually type in a chat.\n"
            )

            compiled_system_instruction = self.system_instruction + time_context + sender_context + tool_constraints

            # Inject sticker catalog instructions
            try:
                catalog_text = "\n\n## Stickers\nTo send a sticker, you MUST first call `search_stickers(emotion, query)` to browse your catalog and find a `sticker_id`. Then call `send_specific_sticker(sticker_id)`. Do NOT use the deprecated `send_sticker` tool.\n"
                compiled_system_instruction += catalog_text
            except Exception as e:
                logger.error(f"Failed to inject sticker catalog instructions: {e}")
            
            local_calls_to_return = []
            final_text = ""
            
            max_turns = 10
            turn = 0
            
            while turn < max_turns:
                turn += 1
                response = self.key_manager.generate_content(
                    model=self.current_api_model,
                    contents=current_contents,
                    config=GenerateContentConfig(
                        system_instruction=compiled_system_instruction,
                        temperature=0.7,
                        tools=all_tools if all_tools else None,
                        automatic_function_calling=AutomaticFunctionCallingConfig(disable=True)
                    )
                )
                
                # Store model's response part
                if response.candidates and response.candidates[0].content:
                    current_contents.append(response.candidates[0].content)
                
                # Accumulate text across all turns
                if response.text:
                    if final_text:
                        final_text += "\n" + response.text
                    else:
                        final_text = response.text
                    
                if not response.function_calls:
                    break
                    
                response_parts = []
                mcp_calls = []
                local_tool_names = [t.__name__ for t in local_tools_list]
                
                for call in response.function_calls:
                    if call.name in local_tool_names:
                        if call.name == ToolName.SEARCH_STICKERS.value:
                            emotion = call.args.get("emotion", "").lower()
                            query = call.args.get("query", "").lower()
                            
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
                                desc = s.get("description", "").lower()
                                em = s.get("emoji", "")
                                if emotion and emotion not in desc and emotion not in em:
                                    continue
                                if query and query not in desc:
                                    continue
                                results.append({
                                    "id": s["_id"],
                                    "emoji": em,
                                    "description": s.get("description", "")
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
                            query = call.args.get("query", "")
                            limit = call.args.get("limit", 10)
                            
                            results = []
                            if query:
                                # Perform a text search in the permanent history collection
                                cursor = chat_context._db.messages.find(
                                    {"chat_id": chat_context.id, "$text": {"$search": query}},
                                    {"score": {"$meta": "textScore"}}
                                ).sort([("score", {"$meta": "textScore"})]).limit(limit)
                                
                                async for msg in cursor:
                                    date_str = msg["date"].strftime("%Y-%m-%d %H:%M") if "date" in msg else "Unknown"
                                    msg_id = msg.get("message_id", "?")
                                    results.append(f"[MsgID: {msg_id}] [{date_str}] {msg.get('role', 'unknown').upper()}: {msg.get('text', '')}")
                                    
                            response_parts.append(
                                Part.from_function_response(
                                    name=call.name,
                                    response={"matches": results if results else ["No matches found."]}
                                )
                            )
                            
                        elif call.name == ToolName.GET_HISTORY_BY_DATE.value:
                            import datetime
                            days_ago = call.args.get("days_ago", 0)
                            limit = call.args.get("limit", 20)
                            
                            target_date = datetime.datetime.utcnow() - datetime.timedelta(days=days_ago)
                            start_of_day = datetime.datetime(target_date.year, target_date.month, target_date.day)
                            end_of_day = start_of_day + datetime.timedelta(days=1)
                            
                            cursor = chat_context._db.messages.find({
                                "chat_id": chat_context.id,
                                "date": {"$gte": start_of_day, "$lt": end_of_day}
                            }).sort("date", -1).limit(limit)
                            
                            results = []
                            async for msg in cursor:
                                date_str = msg["date"].strftime("%Y-%m-%d %H:%M") if "date" in msg else "Unknown"
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
                            reason = call.args.get("reason", "No reason provided")
                            logger.info(f"Model explicitly chose to ignore the message. Reason: {reason}")
                            # Immediately abort the generation loop and return empty
                            return "", []
                            
                        elif call.name == ToolName.SEND_VOICE.value:
                            # We can't generate the voice here, it's done in handlers.
                            # But we should allow handlers to pass the error back if it fails.
                            # Wait, the user wants the AI to generate the contextual text IF TTS fails.
                            # This means TTS generation MUST happen inside ai_service.py, not handlers.py!
                            
                            text_to_speak = call.args.get("text_to_speak", "")
                            
                            from services.tts_service import get_tts_service
                            tts = get_tts_service()
                            
                            try:
                                audio_bytes = await tts.generate_voice(text_to_speak)
                                if audio_bytes:
                                    # Success, pass the call back to handlers to actually send it
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
                                        name = a.user.first_name
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
                            user_id = call.args.get("user_id")
                            fact = call.args.get("fact", "")
                            chat_title = sender_info.get("chat_title", "Unknown Context") if sender_info else "Unknown Context"
                            
                            try:
                                if not user_id or not fact:
                                    raise ValueError("Missing user_id or fact")
                                await chat_context._db.save_user_fact(int(user_id), fact, chat_title)
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
                            user_id = call.args.get("user_id")
                            try:
                                if not user_id:
                                    raise ValueError("Missing user_id")
                                facts = await chat_context._db.get_user_facts(int(user_id))
                                formatted_facts = [f"- {f['date'].strftime('%Y-%m-%d')}: {f['fact']} (Source: {f['source']})" for f in facts]
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
                    logger.info(f"Orchestrating {len(mcp_calls)} remote MCP tool calls...")
                    mcp_parts = await self.mcp_manager.process_function_calls(mcp_calls)
                    response_parts.extend(mcp_parts)
                    
                # Feed responses back into Gemini's generation loop
                current_contents.append(Content(role="user", parts=response_parts))
                
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
