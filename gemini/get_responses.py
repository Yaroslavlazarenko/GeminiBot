from google import genai
from google.genai import types
from google.genai.types import Tool, GenerateContentConfig, GoogleSearch
from typing import List

from copy import deepcopy
from config import Config

config = Config()

client = genai.Client(api_key=config.gemini_api_key)
google_search_tool = Tool(google_search=GoogleSearch())


def read_system_instructions(file_path="system_instructions.txt"):
    try:    
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read().strip()
    except Exception as e:
        return "" 

async def get_gemini_response(contents: List[types.Content]):
    """
    Gets a response from the Gemini model.
    Args:
        contents: A list of google_types.Content objects representing the ENTIRE conversation history.
    """
    system_instruction = read_system_instructions()
    try:
        response = client.models.generate_content(
            model=config.gemini_model,
            contents=contents,
            config=GenerateContentConfig(
                tools=[google_search_tool],
                response_modalities=["text"],
                system_instruction=system_instruction
            )
        )
        if response and response.text:
            return response.text
        else:
            return None
    except Exception:
        return None

async def get_text_response(message_text: str, message_history: List[types.Content]) -> str:
    """Gets a text response from the Gemini model."""
    
    return await get_gemini_response(contents=message_history)

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