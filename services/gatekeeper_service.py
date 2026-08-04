import logging
from core.config import Config
from core.key_manager import get_key_manager
from google.genai.types import GenerateContentConfig, Content, Part, AutomaticFunctionCallingConfig
from pydantic import BaseModel, Field
from typing import List, Dict, Any
from core.enums import GatekeeperAction, ToolName
from core.database import ChatContext
from services.local_tools import gatekeeper_tools_list

logger = logging.getLogger(__name__)

class GatekeeperDecision(BaseModel):
    reasoning: str = Field(description="Brief explanation of why this action was chosen.")
    action: GatekeeperAction = Field(description="The action to take regarding this message.")

class EpochSummary(BaseModel):
    """Structured summary of a conversation epoch for hierarchical history."""
    topics: List[str] = Field(description="3-7 key topics discussed during this epoch, as short noun phrases.")
    participants: List[str] = Field(description="Names of people who participated in this conversation epoch.")
    summary: str = Field(description="Concise summary of the conversation epoch. Keep only the most important context, facts, names, events, and emotional moments. Max 500 words.")

class FactContradictionCheck(BaseModel):
    """Result of checking whether a new fact contradicts existing facts."""
    has_contradiction: bool = Field(description="True if the new fact directly contradicts an existing fact about the same topic.")
    contradicted_fact: str = Field(default="", description="The exact text of the contradicted existing fact, or empty if no contradiction.")
    reasoning: str = Field(description="Brief explanation of the contradiction check result.")

class GatekeeperService:
    def __init__(self, config: Config):
        self.config = config
        self.current_base_url = config.gemini_base_url
        self.current_gatekeeper_model = config.gemini_gatekeeper_model
        
        self.key_manager = get_key_manager()
        
        self.system_instruction = (
            "You are the Gatekeeper for a Telegram bot persona named Mia. "
            "Your job is to read the latest message and the chat history, and decide if Mia should respond.\n\n"
            "Rules:\n"
            "1. Output 'RESPOND' if the message is directed at Mia, asks a question, or requires her input.\n"
            "2. Output 'IGNORE' if the message is casual chatter between other group members, meaningless noise, or explicitly doesn't require a response."
        )

    async def _sync_settings(self, db_manager):
        settings = await db_manager.get_system_settings()
        db_base_url = settings.get("gemini_base_url") or self.config.gemini_base_url
        db_gatekeeper_model = settings.get("gemini_gatekeeper_model") or self.config.gemini_gatekeeper_model
        
        db_api_key = settings.get("gemini_api_key") or self.config.gemini_api_key
        db_api_keys = settings.get("gemini_api_keys") or self.config.gemini_api_keys

        # Update key manager with latest keys pool and base URL
        self.key_manager.update_settings(db_api_key, db_api_keys, db_base_url)
        self.current_base_url = db_base_url
            
        if self.current_gatekeeper_model != db_gatekeeper_model:
            self.current_gatekeeper_model = db_gatekeeper_model

    def _format_history(self, history: List[Dict[str, Any]]) -> str:
        # Keep it lightweight for the gatekeeper
        context_str = ""
        for msg in history[-10:]:  # Only need the last few messages for context
            role = msg.get("role", "unknown")
            text = msg.get("text", "")
            context_str += f"{role}: {text}\n"
        return context_str

    async def decide(self, text: str, chat_context: ChatContext) -> GatekeeperAction:
        """Evaluate if the bot should process this message."""
        await self._sync_settings(chat_context._db)
        
        try:
            history_text = self._format_history(chat_context.history)
            prompt = f"Chat History:\n{history_text}\n\n"
            
            if chat_context.is_group:
                prompt += "ENVIRONMENT: GROUP CHAT (Multiple users).\n"
                prompt += "STRICT RULE: Only output 'RESPOND' if the user EXPLICITLY addresses Mia, replies directly to Mia, asks Mia a direct question, or if the conversation is highly specific to something Mia just participated in. If users are just chatting with each other, output 'IGNORE'. DO NOT intrude on conversations that don't involve you.\n"
                prompt += "If you are unsure whether they are referencing something you talked about recently, you may use the `search_history` or `get_history_by_date` tools to check permanent memory before making your decision.\n\n"
            else:
                prompt += "ENVIRONMENT: PRIVATE CHAT.\n"
                prompt += "RULE: Output 'RESPOND' for normal conversation. Output 'IGNORE' only if it's meaningless spam or just a system notification.\n\n"
                
            prompt += f"New Message: {text}"
            
            current_contents = [Content(role="user", parts=[{"text": prompt}])]
            
            turn = 0
            max_turns = 3
            
            while turn < max_turns:
                turn += 1
                response = self.key_manager.generate_content(
                    model=self.current_gatekeeper_model,
                    contents=current_contents,
                    config=GenerateContentConfig(
                        system_instruction=self.system_instruction,
                        temperature=0.1,
                        tools=gatekeeper_tools_list,
                        automatic_function_calling=AutomaticFunctionCallingConfig(disable=True)
                    )
                )
                
                if response.candidates and response.candidates[0].content:
                    current_contents.append(response.candidates[0].content)
                
                if not response.function_calls:
                    break
                    
                response_parts = []
                for call in response.function_calls:
                    if call.name == ToolName.SEARCH_HISTORY.value:
                        query = call.args.get("query", "")
                        limit = call.args.get("limit", 10)
                        results = []
                        if query:
                            cursor = chat_context._db.messages.find(
                                {"chat_id": chat_context.id, "$text": {"$search": query}},
                                {"score": {"$meta": "textScore"}}
                            ).sort([("score", {"$meta": "textScore"})]).limit(limit)
                            async for msg in cursor:
                                date_str = msg["date"].strftime("%Y-%m-%d %H:%M") if "date" in msg else "Unknown"
                                results.append(f"[{date_str}] {msg.get('role', 'unknown').upper()}: {msg.get('text', '')}")
                        response_parts.append(
                            Part.from_function_response(
                                name=call.name,
                                response={"matches": results if results else ["No matches found."]}
                            )
                        )
                    elif call.name == ToolName.GET_HISTORY_BY_DATE.value:
                        import datetime as dt_module
                        from datetime import timezone, timedelta
                        days_ago = call.args.get("days_ago", 0)
                        limit = call.args.get("limit", 20)
                        # Use Odessa timezone (UTC+3) for day boundaries
                        odessa_tz = timezone(timedelta(hours=3))
                        now_odessa = dt_module.datetime.now(odessa_tz)
                        target_date = now_odessa - dt_module.timedelta(days=days_ago)
                        start_of_day_local = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
                        start_of_day_utc = start_of_day_local.astimezone(timezone.utc).replace(tzinfo=None)
                        end_of_day_utc = start_of_day_utc + dt_module.timedelta(days=1)
                        cursor = chat_context._db.messages.find({
                            "chat_id": chat_context.id,
                            "date": {"$gte": start_of_day_utc, "$lt": end_of_day_utc}
                        }).sort("date", -1).limit(limit)
                        results = []
                        async for msg in cursor:
                            date_str = msg["date"].strftime("%Y-%m-%d %H:%M") if "date" in msg else "Unknown"
                            results.append(f"[{date_str}] {msg.get('role', 'unknown').upper()}: {msg.get('text', '')}")
                        results.reverse()
                        response_parts.append(
                            Part.from_function_response(
                                name=call.name,
                                response={"history": results if results else ["No history found for this date."]}
                            )
                        )
                    else:
                        response_parts.append(
                            Part.from_function_response(
                                name=call.name,
                                response={"error": "Unknown tool."}
                            )
                        )
                current_contents.append(Content(role="user", parts=response_parts))
                
            # Final generation round enforcing the JSON schema after tools are done
            response = self.key_manager.generate_content(
                model=self.current_gatekeeper_model,
                contents=current_contents,
                config=GenerateContentConfig(
                    system_instruction=self.system_instruction,
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=GatekeeperDecision
                )
            )
            
            # The SDK automatically parses the response into the Pydantic object
            decision: GatekeeperDecision = response.parsed
            
            if not decision:
                # Fallback if parsed is empty for some reason
                import json
                raw_text = response.text
                if not raw_text:
                    logger.warning("Gatekeeper: response.text is None, defaulting to RESPOND")
                    return GatekeeperAction.RESPOND
                data = json.loads(raw_text)
                decision = GatekeeperDecision(**data)
            
            logger.info(f"Gatekeeper decided: {decision.action.value} (Reason: {decision.reasoning})")
            return decision.action
            
        except Exception as e:
            logger.error(f"Error in Gatekeeper: {e}", exc_info=True)
            # Failsafe: When in doubt, always respond — ignoring on error makes the bot seem broken/unresponsive
            return GatekeeperAction.RESPOND

    async def summarize_history(self, history: List[Dict[str, Any]]) -> str:
        """Summarize the chat history concisely, removing water."""
        try:
            history_text = ""
            for msg in history:
                role = msg.get("role", "unknown")
                text = msg.get("text", "")
                if text:
                    history_text += f"{role}: {text}\n"

            return await self.summarize_text(history_text)
        except Exception as e:
            logger.error(f"Error summarizing history: {e}")
            return "Failed to generate summary due to an error."

    async def summarize_text(self, raw_text: str) -> str:
        """Summarize arbitrary text concisely. Used for both history and world memory compression."""
        try:
            prompt = (
                "Summarize the following text. "
                "Keep only the most important context, facts, names, and key topics discussed. "
                "Remove all filler words, pleasantries, and 'water'. Make it extremely concise.\n\n"
                f"Text:\n{raw_text}"
            )

            response = self.key_manager.generate_content(
                model=self.current_gatekeeper_model,
                contents=prompt,
                config=GenerateContentConfig(
                    temperature=0.3
                )
            )
            return response.text if response.text else "Summary failed."
        except Exception as e:
            logger.error(f"Error summarizing history: {e}")
            return "Failed to generate summary due to an error."

    async def summarize_history_structured(self, history_messages: List[Dict[str, Any]]) -> 'EpochSummary':
        """Summarize chat history into a structured epoch with topics and participants."""
        try:
            history_text = ""
            for msg in history_messages:
                role = msg.get("role", "unknown")
                text = msg.get("text", "")
                ts = msg.get("timestamp", "")
                if text:
                    history_text += f"[{ts}] {role}: {text}\n"

            prompt = (
                "Analyze and summarize the following conversation history.\n\n"
                "You must identify:\n"
                "1. The key topics discussed (3-7 short noun phrases)\n"
                "2. The names of all participants (extract from [Name]: prefixes or mentions)\n"
                "3. A concise but informative summary (max 500 words) that preserves:\n"
                "   - Important facts and decisions\n"
                "   - Emotional moments and relationship dynamics\n"
                "   - Key events and their outcomes\n"
                "   - Any promises, plans, or commitments made\n\n"
                "Remove all filler, pleasantries, and repetitive exchanges.\n\n"
                f"Conversation:\n{history_text}"
            )

            response = self.key_manager.generate_content(
                model=self.current_gatekeeper_model,
                contents=prompt,
                config=GenerateContentConfig(
                    temperature=0.3,
                    response_mime_type="application/json",
                    response_schema=EpochSummary
                )
            )

            if response.parsed:
                return response.parsed

            # Fallback: try to parse text manually
            if response.text:
                import json
                data = json.loads(response.text)
                return EpochSummary(**data)

            # Last resort fallback
            return EpochSummary(topics=[], participants=[], summary="Summary generation failed.")
        except Exception as e:
            logger.error(f"Error in structured summarization: {e}", exc_info=True)
            # Fallback to plain summarization
            try:
                plain_summary = await self.summarize_history(history_messages)
                return EpochSummary(topics=[], participants=[], summary=plain_summary)
            except Exception:
                return EpochSummary(topics=[], participants=[], summary="Summary generation failed.")

    async def merge_epochs(self, epoch_texts: List[str]) -> str:
        """Merge multiple epoch summaries into one condensed summary."""
        combined = "\n\n---\n\n".join(epoch_texts)
        return await self.summarize_text(combined)

    async def check_fact_contradiction(self, existing_facts: List[Dict[str, Any]], new_fact: str) -> 'FactContradictionCheck':
        """Check if a new fact contradicts any existing facts about a user."""
        try:
            facts_text = "\n".join([f"{i+1}. {f.get('fact', '')}" for i, f in enumerate(existing_facts)])

            prompt = (
                "You are checking whether a NEW fact about a user contradicts any of their EXISTING facts.\n\n"
                f"Existing facts:\n{facts_text}\n\n"
                f"New fact: {new_fact}\n\n"
                "Rules:\n"
                "- Only mark as contradiction if the new fact DIRECTLY contradicts a specific existing fact about the SAME topic.\n"
                "- Example contradiction: 'hates coffee' contradicts 'loves coffee'\n"
                "- NOT a contradiction: 'likes tea' does not contradict 'likes coffee' (different topics)\n"
                "- NOT a contradiction: 'moved to Berlin' does not contradict 'lives in Odessa' — it's an UPDATE, treat as contradiction.\n"
                "- If contradicted, return the EXACT text of the contradicted fact.\n"
                "- Be conservative — when in doubt, mark has_contradiction as false."
            )

            response = self.key_manager.generate_content(
                model=self.current_gatekeeper_model,
                contents=prompt,
                config=GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=FactContradictionCheck
                )
            )

            if response.parsed:
                return response.parsed

            if response.text:
                import json
                data = json.loads(response.text)
                return FactContradictionCheck(**data)

            return FactContradictionCheck(has_contradiction=False, contradicted_fact="", reasoning="Failed to parse response")
        except Exception as e:
            logger.error(f"Error checking fact contradiction: {e}")
            return FactContradictionCheck(has_contradiction=False, contradicted_fact="", reasoning=f"Error: {e}")

# Global instance
_gatekeeper_instance = None

def get_gatekeeper() -> GatekeeperService:
    global _gatekeeper_instance
    if _gatekeeper_instance is None:
        _gatekeeper_instance = GatekeeperService(Config())
    return _gatekeeper_instance
