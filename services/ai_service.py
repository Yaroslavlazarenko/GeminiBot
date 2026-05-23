import logging
import os
from config import Config
from google import genai
from google.genai.types import GenerateContentConfig, FunctionCall
from typing import List, Dict, Any, Tuple
from database.manager import ChatContext
from services.mcp_server import gemini_tools

logger = logging.getLogger(__name__)

class AIService:
    def __init__(self, config: Config):
        self.config = config
        self.client = genai.Client(api_key=config.gemini_api_key)
        self.system_instruction = self._load_system_instructions()
        
    def _load_system_instructions(self) -> str:
        try:
            with open("system_instructions.md", "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Failed to load system_instructions.md: {e}")
            return "You are Mia Zareva."

    def _convert_history_to_gemini(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert MongoDB history dicts to Gemini API format."""
        formatted = []
        for msg in history:
            role = "user" if msg.get("role") == "user" else "model"
            text = msg.get("text", "")
            
            # Note: We omit tool calls from history here to keep the history lightweight,
            # but we could serialize them if needed in the future.
            if text:
                formatted.append({
                    "role": role,
                    "parts": [{"text": text}]
                })
        return formatted

    async def generate_response(self, text: str, chat_context: ChatContext) -> Tuple[str, List[FunctionCall]]:
        """Generate a response using Gemini based on the ChatContext. Returns (text, tool_calls)."""
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
                    system_instruction=self.system_instruction,
                    temperature=0.7,
                    tools=gemini_tools  # Automatically mapped to Gemini FunctionDeclarations!
                )
            )
            
            # Extract text and any tool calls the model decided to make natively
            response_text = response.text if response.text else ""
            tool_calls = []
            
            if response.function_calls:
                tool_calls = response.function_calls
                
            return response_text, tool_calls
            
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
