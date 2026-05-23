import logging
from config import Config
from database.manager import DatabaseManager
from google import genai
from google.genai.types import GenerateContentConfig
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class AIService:
    def __init__(self, config: Config):
        self.config = config
        self.client = genai.Client(api_key=config.gemini_api_key)
        
    def _convert_history_to_gemini(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert MongoDB history dicts to Gemini API format."""
        formatted = []
        for msg in history:
            role = msg.get("role")
            text = msg.get("text")
            
            # Map roles if needed
            if role == "user":
                formatted_role = "user"
            else:
                formatted_role = "model"
                
            formatted.append({
                "role": formatted_role,
                "parts": [{"text": text}]
            })
        return formatted

    async def generate_response(self, text: str, history: List[Dict[str, Any]]) -> str:
        """Generate a response using Gemini."""
        try:
            gemini_history = self._convert_history_to_gemini(history)
            
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
