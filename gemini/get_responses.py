from google import genai
from google.genai import types
from google.genai.types import Tool, GenerateContentConfig, GoogleSearch

import io
import logging

from config import Config
config = Config()

client = genai.Client(api_key=config.gemini_api_key)
google_search_tool = Tool(google_search=GoogleSearch())

def read_system_instructions(file_path="system_instructions.txt"):
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read().strip()
    except Exception as e:
        logging.error(f"Error reading system instructions: {e}")
        return ""  # Возвращаем пустую строку в случае ошибки

async def get_gemini_response(contents):
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

        if response:
            return response.text
        else:
            return None
    except Exception as e:
        return None

async def get_text_response(message_text):
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=message_text),
            ]
        ),
    ]
    return await get_gemini_response(contents=contents)


async def get_audio_response(audio_file, response=None):
    audio_bytes = audio_file  # Directly use audio_file as bytes

    if(response):
        text = "Ответь на голосовой"
    else:
        text="Transcribe the text completely, repeat only the words in the language that was said. Answer only with the text of the voice."

    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=text),
                types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg")
            ]
        ),
    ]

    return await get_gemini_response(contents=contents)
