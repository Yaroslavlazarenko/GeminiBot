import logging
from core.config import Config
from core.key_manager import get_key_manager
from google.genai.types import GenerateContentConfig
from pydantic import BaseModel, Field
from typing import List, Dict, Any
from core.enums import GatekeeperAction
from core.database import ChatContext

logger = logging.getLogger(__name__)

class GatekeeperDecision(BaseModel):
    reasoning: str = Field(description="Brief explanation of why this action was chosen.")
    action: GatekeeperAction = Field(description="The action to take regarding this message.")

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
            prompt = f"Chat History:\n{history_text}\n\nNew Message: {text}"
            
            response = self.key_manager.generate_content(
                model=self.current_gatekeeper_model,
                contents=prompt,
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
                data = json.loads(response.text)
                decision = GatekeeperDecision(**data)
            
            logger.info(f"Gatekeeper decided: {decision.action.value} (Reason: {decision.reasoning})")
            return decision.action
            
        except Exception as e:
            logger.error(f"Error in Gatekeeper: {e}")
            # Fallback to respond if gatekeeper fails, so we don't break the bot
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

            prompt = (
                "Summarize the following chat history. "
                "Keep only the most important context, facts, names, and key topics discussed. "
                "Remove all filler words, pleasantries, and 'water'. Make it extremely concise.\n\n"
                f"History:\n{history_text}"
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

# Global instance
_gatekeeper_instance = None

def get_gatekeeper() -> GatekeeperService:
    global _gatekeeper_instance
    if _gatekeeper_instance is None:
        _gatekeeper_instance = GatekeeperService(Config())
    return _gatekeeper_instance
