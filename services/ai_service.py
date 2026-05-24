import logging
import os
from core.config import Config
from google import genai
from google.genai.types import GenerateContentConfig, FunctionCall, Content, Tool
from typing import List, Dict, Any, Tuple
from core.database import ChatContext
from services.local_tools import local_tools_list
from services.mcp_manager import MCPConnectionManager

logger = logging.getLogger(__name__)

class AIService:
    def __init__(self, config: Config):
        self.config = config
        self.current_base_url = config.gemini_base_url
        self.current_api_model = config.gemini_api_model
        
        http_opts = {"base_url": self.current_base_url} if self.current_base_url else None
        self.client = genai.Client(api_key=config.gemini_api_key, http_options=http_opts)
        
        self.system_instruction = "You are Mia Zareva." # Will be updated from DB
        
        self.current_mcp_config = config.mcp_servers_config
        self.mcp_manager = MCPConnectionManager(self.current_mcp_config)

    def _convert_history_to_gemini(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        formatted = []
        for msg in history:
            role = "user" if msg.get("role") == "user" else "model"
            text = msg.get("text", "")
            if text:
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

        if db_sys_prompt and self.system_instruction != db_sys_prompt:
            self.system_instruction = db_sys_prompt
            
        if self.current_base_url != db_base_url:
            logger.info("Detected base_url change, recreating Gemini client...")
            self.current_base_url = db_base_url
            http_opts = {"base_url": db_base_url} if db_base_url else None
            self.client = genai.Client(api_key=self.config.gemini_api_key, http_options=http_opts)
            
        if self.current_api_model != db_api_model:
            logger.info(f"Detected api_model change to {db_api_model}")
            self.current_api_model = db_api_model
            
        if self.current_mcp_config != db_mcp_config:
            logger.info("Detected MCP config change, recreating MCP Manager...")
            await self.mcp_manager.close()
            self.mcp_manager = MCPConnectionManager(db_mcp_config)
            self.current_mcp_config = db_mcp_config

    async def generate_response(self, text: str, chat_context: ChatContext) -> Tuple[str, List[FunctionCall]]:
        """Generate a response using Gemini based on the ChatContext. Returns (text, local_tool_calls)."""
        await self._sync_settings(chat_context._db)
        await self.mcp_manager.connect()
        
        all_tools = []
        if local_tools_list:
            all_tools.append(local_tools_list)
        if self.mcp_manager.mcp_declarations:
            all_tools.append(Tool(function_declarations=self.mcp_manager.mcp_declarations))
            
        try:
            gemini_history = self._convert_history_to_gemini(chat_context.history)
            current_contents = gemini_history + [{"role": "user", "parts": [{"text": text}]}]
            
            local_calls_to_return = []
            final_text = ""
            
            max_turns = 10
            turn = 0
            
            while turn < max_turns:
                turn += 1
                response = self.client.models.generate_content(
                    model=self.current_api_model,
                    contents=current_contents,
                    config=GenerateContentConfig(
                        system_instruction=self.system_instruction,
                        temperature=0.7,
                        tools=all_tools if all_tools else None
                    )
                )
                
                # Store model's response part
                if response.candidates and response.candidates[0].content:
                    current_contents.append(response.candidates[0].content)
                    
                if not response.function_calls:
                    final_text = response.text if response.text else ""
                    break
                    
                mcp_calls = []
                local_tool_names = [t.__name__ for t in local_tools_list]
                
                for call in response.function_calls:
                    if call.name in local_tool_names:
                        local_calls_to_return.append(call)
                    else:
                        mcp_calls.append(call)
                        
                if not mcp_calls:
                    # Only local tools (terminal UI actions) were called
                    final_text = response.text if response.text else ""
                    break
                    
                # Execute remote MCP tools and feed back to model
                logger.info(f"Orchestrating {len(mcp_calls)} remote MCP tool calls...")
                response_parts = await self.mcp_manager.process_function_calls(mcp_calls)
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
