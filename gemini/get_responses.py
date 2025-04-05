import logging # Добавить импорт логгера
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

combined_tool = Tool(
    function_declarations=[do_not_respond_func, disable_responses],
    google_search=GoogleSearch()
)

tools_to_pass_in_list = [combined_tool]

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
        # logger.debug(f"Received Gemini response: {response}") # Логирование полного ответа (может быть большим)

        # Проверяем первый кандидат (самый вероятный)
        if response.candidates:
            candidate = response.candidates[0]

            # 1. Проверяем блокировку из-за безопасности
            if candidate.finish_reason.name != "STOP" and candidate.finish_reason.name != "TOOL_CODE":
                 # FINISH_REASON_SAFETY, FINISH_REASON_RECITATION, FINISH_REASON_OTHER, etc.
                 logger.warning(f"Gemini generation finished with non-STOP reason: {candidate.finish_reason.name}")
                 # Если есть safety_ratings, логируем их
                 if candidate.safety_ratings:
                     logger.warning(f"Safety Ratings: {candidate.safety_ratings}")
                 # Не возвращаем контент, если он заблокирован или не остановился нормально
                 # Можно вернуть специфическую ошибку или no_response
                 return {"type": "no_response", "data": f"Generation stopped: {candidate.finish_reason.name}"}


            # 2. Проверяем наличие контента у кандидата
            if candidate.content and candidate.content.parts:
                part = candidate.content.parts[0] # Обычно все в первой части

                # 3. Проверяем вызов функции
                if part.function_call:
                    function_call = part.function_call
                    fc_data = {"name": function_call.name, "args": dict(function_call.args)}
                    logger.info(f"Gemini requested function call: {fc_data['name']}")
                    # НЕ ВЫПОЛНЯЕМ ДЕЙСТВИЕ ЗДЕСЬ
                    # Просто возвращаем информацию о вызове
                    return {"type": "function_call", "data": fc_data}

                # 4. Если не функция, проверяем наличие текста
                # Используем response.text как удобный способ получить весь текст
                elif response.text:
                    logger.info(f"Gemini returned text response (length: {len(response.text)})")
                    return {"type": "text", "data": response.text.strip()}

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
        contents=message_history,
        user=user,
        task_hint=task
    )