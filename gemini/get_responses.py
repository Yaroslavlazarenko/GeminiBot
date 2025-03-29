from google import genai
from google.genai import types
from google.genai.types import Tool, GenerateContentConfig, GoogleSearch

import logging

from config import Config
config = Config()

client = genai.Client(api_key=config.gemini_api_key)

async def get_text_response(message_text):
    try:
        response = client.models.generate_content(
            model=config.gemini_model,
            contents=message_text,
            config=GenerateContentConfig(
                response_modalities=["text"]
            )
        )

        if not response or not response.candidates or not response.candidates[0].content.parts:
            return None
        
        # Обрабатываем обычный текстовый ответ
        textResponse = "".join(part.text for part in response.candidates[0].content.parts if hasattr(part, "text") and part.text)

        if not textResponse.strip():
            return None
        
        return textResponse

    except Exception as e:
        return None


#async def get_transcription(message,)