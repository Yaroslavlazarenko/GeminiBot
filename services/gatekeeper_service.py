import logging
from config import Config
from google import genai
from google.genai.types import GenerateContentConfig
from pydantic import BaseModel, Field
from typing import List, Dict, Any
from core.enums import GatekeeperAction
from database.manager import ChatContext

logger = logging.getLogger(__name__)

class GatekeeperDecision(BaseModel):
    reasoning: str = Field(description="Brief explanation of why this action was chosen.")
    action: GatekeeperAction = Field(description="The action to take regarding this message.")

class GatekeeperService:
    def __init__(self, config: Config):
        self.config = config
        self.client = genai.Client(api_key=config.gemini_api_key)
        self.system_instruction = (
            "You are the Gatekeeper for a Telegram bot persona named Mia. "
            "Your job is to read the latest message and the chat history, and decide if Mia should respond.\n\n"
            "Rules:\n"
            "1. Output 'RESPOND' if the message is directed at Mia, asks a question, or requires her input.\n"
            "2. Output 'IGNORE' if the message is casual chatter between other group members, meaningless noise, or explicitly doesn't require a response.\n"
            "3. Output 'DISABLE_RESPONSES' if the user is seriously offended, extremely toxic, or explicitly demands the bot to shut up and stop responding permanently."
        )

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
        try:
            history_text = self._format_history(chat_context.history)
            prompt = f"Chat History:\n{history_text}\n\nNew Message: {text}"
            
            response = self.client.models.generate_content(
                model=self.config.gemini_gatekeeper_model,
                contents=prompt,
                config=GenerateContentConfig(
                    system_instruction=self.system_instruction,
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=GatekeeperDecision
                )
            )
            
            # The SDK parses the JSON into a dictionary or Pydantic object if response_schema is used
            # But just to be safe if it returns raw text matching the schema:
            decision_text = response.text
            import json
            data = json.loads(decision_text)
            
            action = GatekeeperAction(data.get("action", GatekeeperAction.RESPOND.value))
            logger.info(f"Gatekeeper decided: {action.value} (Reason: {data.get('reasoning', '')})")
            return action
            
        except Exception as e:
            logger.error(f"Error in Gatekeeper: {e}")
            # Fallback to respond if gatekeeper fails, so we don't break the bot
            return GatekeeperAction.RESPOND

# Global instance
_gatekeeper_instance = None

def get_gatekeeper() -> GatekeeperService:
    global _gatekeeper_instance
    if _gatekeeper_instance is None:
        _gatekeeper_instance = GatekeeperService(Config())
    return _gatekeeper_instance
