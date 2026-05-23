import logging
from config import Config
from google import genai
from google.genai.types import GenerateContentConfig
from typing import List, Dict, Any
from database.manager import ChatContext

logger = logging.getLogger(__name__)

class AIService:
    def __init__(self, config: Config):
        self.config = config
        self.client = genai.Client(api_key=config.gemini_api_key)
        
    def _convert_history_to_gemini(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert MongoDB history dicts to Gemini API format."""
        formatted = []
        for msg in history:
            role = "user" if msg.get("role") == "user" else "model"
            text = msg.get("text", "")
                
            formatted.append({
                "role": role,
                "parts": [{"text": text}]
            })
        return formatted

    async def generate_response(self, text: str, chat_context: ChatContext) -> str:
        """Generate a response using Gemini based on the ChatContext."""
        try:
            gemini_history = self._convert_history_to_gemini(chat_context.history)
            
            # Append current message to history for the API call
            gemini_history.append({
                "role": "user",
                "parts": [{"text": text}]
            })
            
            response = self.client.models.generate_content(
                model=self.config.gemini_api_model,
                contents=gemini_history,
                config=GenerateContentConfig(
                    temperature=0.7,
                )
            )
            return response.text
        except Exception as e:
            logger.error(f"Error generating AI response: {e}")
            return "Sorry, I encountered an error while processing your request."

# Global instance initialized lazily or at module load
_ai_service_instance = None

def get_ai_service() -> AIService:
    global _ai_service_instance
    if _ai_service_instance is None:
        _ai_service_instance = AIService(Config())
    return _ai_service_instance
