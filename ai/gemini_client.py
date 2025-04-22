import logging
import re
from google import genai
from google.genai import types
from google.genai.types import Tool, GenerateContentConfig, GoogleSearch
from typing import List, Dict, Any
from config import Config
from datetime import datetime
import pytz
import asyncio
from google.genai.errors import ServerError

from database.models import User

logger = logging.getLogger(__name__)

config = Config()

# Retry configuration
MAX_RETRIES = 3
BASE_DELAY = 1  # Base delay in seconds
MAX_DELAY = 10  # Maximum delay in seconds

try:
    client = genai.Client(api_key=config.gemini_api_key)
    async_client = client.aio
    logger.info("Gemini Async Client initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize Gemini client: {e}", exc_info=True)
    async_client = None

search_tool = Tool(google_search=GoogleSearch())
tools_to_pass_in_list = [search_tool]

def read_system_instructions(file_path="system_instructions.txt") -> str:
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read().strip()
    except FileNotFoundError:
        logger.warning(f"System instructions file not found at {file_path}. Using empty instructions.")
        return ""
    except Exception as e:
        logger.error(f"Error reading system instructions from {file_path}: {e}", exc_info=True)
        return ""

def get_current_time_str(timezone_str: str = "Europe/Kiev") -> str:
    try:
        tz = pytz.timezone(timezone_str)
        now = datetime.now(tz)
        return now.strftime('%Y-%m-%d %H:%M:%S %Z%z')
    except Exception as e:
        logger.error(f"Error getting timezone {timezone_str}: {e}. Falling back to naive datetime.")
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S (Unknown Timezone)')

async def get_gemini_response(
    contents: List[types.Content],
    user: User,
    task_hint: str | None = None
) -> Dict[str, Any]:
    """
    Gets a response from the Gemini model with retry logic for server errors.

    Args:
        contents: Conversation history.
        user: The user object.
        task_hint: Specific instruction for the current turn.

    Returns:
        A dictionary with the response type and data:
        - {"type": "json_response", "data": {"text": "response text", "commands": [...]}}
        - {"type": "error", "data": "Error message"}
    """
    if not async_client:
        logger.warning("Gemini async client not initialized.")
        return {"type": "error", "data": "Gemini client not available"}

    # Add critical JSON formatting instruction to context
    critical_instruction = types.Content(
        parts=[types.Part(text="""CRITICAL: YOU MUST RETURN ONLY A SINGLE JSON OBJECT AS YOUR COMPLETE RESPONSE.
DO NOT FORMAT IT AS CODE. DO NOT ADD ANY MARKDOWN. NO BACKTICKS. NO EXPLANATION TEXT.
JUST THE RAW JSON OBJECT. YOUR ENTIRE RESPONSE MUST BE PARSEABLE AS JSON.""")],
        role="user"
    )
    
    # Add instruction to the start of the context
    contents = [critical_instruction] + contents

    base_instructions = read_system_instructions()
    current_time = get_current_time_str()

    # Add user-specific context to system instructions
    system_prompt_parts = [base_instructions]
    system_prompt_parts.append(f"\nCurrent time: {current_time}")
    system_prompt_parts.append(f"\nCurrent user ID: {user.telegram_id}")
    
    # Add group context if available
    if any(c.role == "system" and "group chat" in c.parts[0].text for c in contents):
        system_prompt_parts.append("\nIMPORTANT: You are in a group chat. Keep your responses concise and relevant to the current user's message. Avoid long conversations or complex interactions.")
    
    system_prompt_parts.append("\nIMPORTANT: Your responses and reactions should be specific to the current user. If you choose to disable responses or react negatively, it should only affect this specific user. Previous negative interactions with other users should not influence your response to the current user.")
    
    if task_hint:
        system_prompt_parts.append(f"\nSpecific instruction for this turn: {task_hint}")
    system_prompt = "\n".join(filter(None, system_prompt_parts))

    retries = 0
    while retries < MAX_RETRIES:
        try:
            logger.debug(f"Sending request to Gemini (attempt {retries + 1}/{MAX_RETRIES}). History length: {len(contents)}. Task hint: {task_hint}")

            response = await async_client.models._generate_content(
                model=config.gemini_model,
                contents=contents,
                config=GenerateContentConfig(
                    tools=tools_to_pass_in_list,
                    response_modalities=["text"],
                    system_instruction=system_prompt,
                )
            )

            if not response or not response.text:
                logger.warning("Gemini response is empty or None.")
                return {"type": "error", "data": "Empty response from Gemini"}

            try:
                # Clean the response and process JSON as before
                raw_text = response.text.strip()
                clean_text = re.sub(r'```(?:json)?\n?', '', raw_text)
                clean_text = clean_text.strip()
                
                def extract_json_object(text):
                    # Ищем первую открывающую скобку
                    start = text.find('{')
                    if start == -1:
                        return None
                    
                    # Отслеживаем вложенность скобок
                    count = 0
                    for i in range(start, len(text)):
                        if text[i] == '{':
                            count += 1
                        elif text[i] == '}':
                            count -= 1
                            if count == 0:
                                # Нашли соответствующую закрывающую скобку
                                return text[start:i + 1]
                    return None

                # Извлекаем первый полный JSON объект
                json_text = extract_json_object(clean_text)
                if json_text:
                    clean_text = json_text

                # Парсим JSON ответ от модели
                import json
                response_json = json.loads(clean_text)
                
                # Проверяем структуру
                if not isinstance(response_json, dict):
                    raise ValueError("Response JSON must be an object")
                
                text = response_json.get("text", "").strip()
                commands = response_json.get("commands", [])

                # Validate commands structure
                for command in commands:
                    if not isinstance(command, dict):
                        continue
                    if "name" not in command or "args" not in command:
                        continue
                    
                    # Special validation for add_reaction command
                    if command["name"] == "add_reaction":
                        args = command["args"]
                        if not isinstance(args, dict):
                            continue
                        if "emoji" not in args or not args["emoji"]:
                            continue
                        if "message_ids" not in args or not isinstance(args["message_ids"], list):
                            command["args"]["message_ids"] = []

                # Даже если нет текста, могут быть команды
                return {
                    "type": "json_response",
                    "data": {
                        "text": text,
                        "commands": commands
                    }
                }
                
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse response as JSON: {e}. Response: {raw_text[:200]}...")
                return {
                    "type": "json_response",
                    "data": {
                        "text": raw_text.strip(),
                        "commands": []
                    }
                }

        except ServerError as e:
            # Extract status code from error message if available
            status_code = 500  # Default to 500 if not found
            if hasattr(e, 'error') and isinstance(e.error, dict):
                status_code = e.error.get('code', 500)
            
            if status_code == 500 and retries < MAX_RETRIES - 1:
                delay = min(BASE_DELAY * (2 ** retries), MAX_DELAY)  # Exponential backoff
                logger.warning(f"Gemini API 500 error (attempt {retries + 1}/{MAX_RETRIES}), retrying in {delay}s: {e}")
                await asyncio.sleep(delay)
                retries += 1
                continue
            logger.error(f"Gemini API server error after {retries + 1} attempts: {e}")
            return {"type": "error", "data": f"Gemini API Server Error: {e}"}
            
        except Exception as e:
            logger.error(f"Error during Gemini API call: {e}", exc_info=True)
            return {"type": "error", "data": f"Gemini API Error: {e}"}
        
        break  # If we get here, the request was successful

    # If we exhausted all retries
    if retries == MAX_RETRIES:
        return {"type": "error", "data": "Maximum retry attempts reached for Gemini API"}

async def get_text_response(
    message_history: List[types.Content],
    user: User
) -> Dict[str, Any]:
    """Gets a text response from the Gemini model for general conversation."""
    logger.debug(f"Getting text response for user {user.telegram_id}")
    return await get_gemini_response(contents=message_history, user=user)

async def get_audio_response(
    message_history: List[types.Content],
    user: User,
    response: bool = False # Флаг: True=ответить, False=транскрибировать
) -> Dict[str, Any]:
    """Gets a response/transcription for audio from the Gemini model."""
    if response:
        task = "Respond helpfully in text to the content of the last user message, which contains audio data. For formatting use html formatting"
        logger.debug(f"Getting audio RESPONSE for user {user.telegram_id}")
    else:
        task = "Transcribe the text completely from the audio data in the last user message. Repeat only the words in the language that was said. Answer ONLY with the transcribed text."
        logger.debug(f"Getting audio TRANSCRIPTION for user {user.telegram_id}")

    return await get_gemini_response(
        contents=message_history,
        user=user,
        task_hint=task
    )

async def get_video_response(
    message_history: List[types.Content],
    user: User,
    response: bool = True
) -> Dict[str, Any]:
    """
    Processes video notes and returns Gemini's response.
    When response=True, generates a full response to the video.
    When response=False, only transcribes/describes the video.
    """
    task_hint = None if response else "Describe what you see in the video note. Don't respond to it, just describe what you see."
    return await get_gemini_response(message_history, user, task_hint)