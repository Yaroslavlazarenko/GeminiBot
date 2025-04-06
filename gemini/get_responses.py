import logging # Добавить импорт логгера
import re
from google import genai
from google.genai import types
from google.genai.types import Tool, GenerateContentConfig, GoogleSearch, FunctionDeclaration
from typing import List, Dict, Any, Literal # Добавить Literal
from config import Config
from datetime import datetime
import pytz

# Предполагается, что модель User импортирована
from services.database.models import User

# Настроим логгер
logger = logging.getLogger(__name__)

config = Config()

try:
    client = genai.Client(api_key=config.gemini_api_key)
    async_client = client.aio
    logger.info("Gemini Async Client initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize Gemini client: {e}", exc_info=True)
    async_client = None

# --- Функции и Инструменты (без изменений) ---
do_not_respond_func = FunctionDeclaration(
    name="do_not_respond",
    description="This is your primary tool when you no longer want to reply. Use this tool when you decide that a text reply is not necessary or appropriate for a user's message. For example, if the message is meaningless, offensive (as part of a security policy), or if the user explicitly asks not to reply.",
    parameters=None,
)

disable_responses = FunctionDeclaration(
    name="disable_responses",
    description="If a user is seriously offended or asked to shut up and not respond anymore",
    parameters=None,
)

search_tool = Tool(google_search=GoogleSearch())

# 2. Создаем инструмент для Function Calling
function_tool = Tool(
    function_declarations=[do_not_respond_func, disable_responses]
)

tools_to_pass_in_list = [search_tool]

# --- Утилиты (без изменений) ---
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


def check_text_for_function_call(text: str):
    """
    Checks text content for a 'functions{...}' block containing specific
    function calls like 'do_not_respond: true' or 'disable_responses: true'.

    Args:
        text: The text string to check.

    Returns:
        The name of the first matched function ("do_not_respond" or
        "disable_responses") if found with status 'true', otherwise None.
        Priority is given based on the order of checks if multiple are present.
    """
    if not text:
        return None

    # Regex to find the functions block and capture its content (case-insensitive, multiline)
    block_match = re.search(r"!functions\s*\{\s*(.*?)\s*\}", text, re.IGNORECASE | re.DOTALL)

    if not block_match:
        return None # No 'functions{...}' block found

    content = block_match.group(1).strip()
    logger.debug(f"Found 'functions' block in text response. Content: '{content}'")

    # Regex to parse lines like "FunctionName: status" inside the block (case-insensitive)
    call_matches = re.findall(r"([\w_]+)\s*:\s*(true|false|\w+)", content, re.IGNORECASE)

    if not call_matches and content:
        logger.warning(f"Found functions block in text, but could not parse calls like 'Name: status' inside.")
        return None

    # Check for specific functions in order of priority if needed
    for func_name, status in call_matches:
        func_name_cleaned = func_name.strip().lower()
        status_cleaned = status.strip().lower()

        # Check against the DECLARED function names
        if func_name_cleaned == "do_not_respond" and status_cleaned == "true":
            logger.info("Detected 'do_not_respond: true' within the text response.")
            return "do_not_respond" # Return the function name

        if func_name_cleaned == "disable_responses" and status_cleaned == "true":
            logger.info("Detected 'disable_responses: true' within the text response.")
            return "disable_responses" # Return the function name

    # If the loop finishes without finding the specific calls
    return None

# --- Основная функция взаимодействия с API ---

# Определяем возможные типы возвращаемых значений для лучшей типизации
ResponseType = Literal["text", "function_call", "error", "no_response"]
FunctionCallResult = Dict[str, Any] # Например: {"name": "func_name", "args": {...}}

async def get_gemini_response(
    contents: List[types.Content],
    user: User, # Оставляем user, т.к. он может влиять на системный промпт или логику в будущем
    task_hint: str | None = None # Добавляем подсказку для задачи
) -> Dict[str, Any]:
    """
    Gets a response from the Gemini model, returning structured information.

    Args:
        contents: Conversation history.
        user: The user object (can be used for personalization if needed).
        task_hint: Specific instruction for the current turn (e.g., 'Transcribe audio').

    Returns:
        A dictionary indicating the result type and data:
        - {"type": "text", "data": "response text"}
        - {"type": "function_call", "data": {"name": "func_name", "args": {...}}}
        - {"type": "no_response"} (e.g., do_not_respond called or no content)
        - {"type": "error", "data": "Error message"}
    """
    if not async_client:
        logger.warning("Gemini async client not initialized.")
        return {"type": "error", "data": "Gemini client not available"}

    base_instructions = read_system_instructions()
    current_time = get_current_time_str()

    # Формируем системный промпт, добавляя подсказку, если она есть
    system_prompt_parts = [base_instructions]
    system_prompt_parts.append(f"\nCurrent time: {current_time}")
    if task_hint:
        system_prompt_parts.append(f"\nSpecific instruction for this turn: {task_hint}")
    system_prompt = "\n".join(filter(None, system_prompt_parts)) # Собираем непустые части

    try:
        logger.debug(f"Sending request to Gemini. History length: {len(contents)}. Task hint: {task_hint}")
        # logger.debug(f"System prompt: {system_prompt[:200]}...") # Логируем начало промпта

        response = await async_client.models._generate_content(
            model=config.gemini_model,
            contents=contents,
            config=GenerateContentConfig(
                tools=tools_to_pass_in_list,
                response_modalities=["text"],
                system_instruction=system_prompt
            )
        )

        if not response:
            logger.warning("Gemini response is empty or None.")
            return {"type": "no_response"}
        
        response_text = response.text
        if response_text:
            # Check the text content for our special function directives
            function_called_in_text = check_text_for_function_call(response_text)

            if function_called_in_text == "do_not_respond":
                # The text itself contains the instruction not to respond
                logger.info("Overriding text response based on 'do_not_respond: true' found in text content.")
                fc_data = {"name": "do_not_respond", "args": {}}
                return {"type": "function_call", "data": fc_data}
            elif function_called_in_text == "disable_responses":
                # The text contains the instruction to disable responses
                logger.info("Detected 'disable_responses: true' in text content. Returning as function call.")
                # Return it like a structured function call for upstream handling
                # Assuming no args needed based on declaration
                fc_data = {"name": "disable_responses", "args": {}}
                return {"type": "function_call", "data": fc_data}
            else:
                # No special function directive found in text, return the text normally
                logger.debug("Returning regular text response.")
                return {"type": "text", "data": response_text}
        

        # Если нет кандидатов или нет подходящего контента/функции
        logger.warning("No suitable content or function call found in Gemini response.")
        return {"type": "no_response"}

    except Exception as e:
        logger.error(f"Error during Gemini API call: {e}", exc_info=True)
        return {"type": "error", "data": f"Gemini API Error: {e}"}

# --- Обертки для конкретных задач ---

async def get_text_response(
    # message_text: str, # Параметр можно убрать, если история всегда актуальна
    message_history: List[types.Content],
    user: User
) -> Dict[str, Any]:
    """Gets a text response from the Gemini model for general conversation."""
    logger.debug(f"Getting text response for user {user.telegram_id}")
    # Передаем историю как есть, без специальной подсказки
    return await get_gemini_response(contents=message_history, user=user)

async def get_audio_response(
    # audio_file: bytes, # Сами байты здесь не нужны, они должны быть в истории
    message_history: List[types.Content], # История должна содержать аудио Part
    user: User,
    response: bool = False # Флаг: True=ответить, False=транскрибировать
) -> Dict[str, Any]:
    """Gets a response/transcription for audio from the Gemini model."""
    # НЕ МОДИФИЦИРУЕМ ИСТОРИЮ ЗДЕСЬ

    if response:
        # Просим модель ответить на последнее сообщение (которое должно быть аудио)
        task = "Respond helpfully in text to the content of the last user message, which contains audio data."
        logger.debug(f"Getting audio RESPONSE for user {user.telegram_id}")
    else:
        # Просим модель транскрибировать последнее сообщение
        task = "Transcribe the text completely from the audio data in the last user message. Repeat only the words in the language that was said. Answer ONLY with the transcribed text."
        logger.debug(f"Getting audio TRANSCRIPTION for user {user.telegram_id}")

    # Передаем оригинальную историю и подсказку для задачи
    return await get_gemini_response(
        contents= message_history,
        user=user,
        task_hint=task
    )