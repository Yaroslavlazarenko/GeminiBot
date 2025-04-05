from google import genai
from google.genai import types
from google.genai.types import Tool, GenerateContentConfig, GoogleSearch, FunctionDeclaration
from typing import List
from config import Config
from datetime import datetime
import pytz

from services.database.models import User

config = Config()

try:
    # Используйте client = genai.Client() если API ключ установлен через genai.configure()
    # или переменные окружения
    client = genai.Client(api_key=config.gemini_api_key)
    # Получаем асинхронный интерфейс
    async_client = client.aio
except Exception as e:
    # Обработайте ошибку инициализации по вашему усмотрению
    # Например, можно завершить работу скрипта: exit()
    async_client = None # Или установите в None, чтобы проверки ниже сработали

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

# Инструмент для функции
combined_tool = Tool(
    function_declarations=[do_not_respond_func, disable_responses],
    google_search=GoogleSearch()  # Добавляем поиск СЮДА
)

tools_to_pass_in_list = [combined_tool]

def read_system_instructions(file_path="system_instructions.txt"):
    try:    
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read().strip()
    except Exception as e:
        return "" 

def get_current_time_str(timezone_str: str = "Europe/Kiev") -> str:
    """Возвращает текущее время в виде строки с указанием таймзоны."""
    try:
        tz = pytz.timezone(timezone_str)
        now = datetime.now(tz)
        # Формат можно настроить по желанию
        return now.strftime('%Y-%m-%d %H:%M:%S %Z%z')
    except Exception as e:
        # Возвращаем запасной вариант без таймзоны, если pytz не сработал
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S (Unknown Timezone)')


async def get_gemini_response(contents: List[types.Content], user: User):
    """
    Gets a response from the Gemini model.
    Args:
        contents: A list of google_types.Content objects representing the ENTIRE conversation history.
    """
    if not async_client:
         return None
    
    base_instructions = read_system_instructions()
    current_time = get_current_time_str()
    system_prompt = f"{base_instructions}\n\nТекущее время: {current_time}"
    try:
        response = await async_client.models._generate_content(
            model=config.gemini_model,
            contents=contents,
            config=GenerateContentConfig(
                tools=tools_to_pass_in_list,
                response_modalities=["text"],
                system_instruction=system_prompt
            )
        )
        if response and response.candidates:
            # Usually, we check the first candidate
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                part = candidate.content.parts[0]
                if part.function_call:
                    function_call = part.function_call
                    if function_call.name == "do_not_respond":
                        return None  # Function called, no text response needed
                    if function_call.name == "disable_responses":
                        user.responds_to_text = not user.responds_to_text
                        return None
                # Check for text ONLY if it wasn't a function call (or handle function call first)
                elif response.text: # response.text is a convenient shortcut
                    return response.text
        return None
    except Exception:
        return None

async def get_text_response(message_text: str, message_history: List[types.Content], user: User) -> str:
    """Gets a text response from the Gemini model."""
    
    return await get_gemini_response(contents=message_history, user=user)

async def get_audio_response(audio_file: bytes, message_history: List[types.Content], response: bool = False) -> str:
    """Gets an audio response from the Gemini model."""
    if response:
        text = "Следующее сообщение это голосовое сообщение, или же аудиосообщение, ответь на него:"
    else:
        text = "Следующее сообщение это голосовое сообщение, или же аудиосообщение. Transcribe the text completely, repeat only the words in the language that was said. Answer only with the text of the voice."

    new_content = types.Content(
        role="user",
        parts=[
            types.Part.from_text(text=text),
        ]
    )
    updated_history = message_history + [new_content]
    return await get_gemini_response(contents=updated_history)