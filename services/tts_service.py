import logging
import asyncio
from typing import Optional
from elevenlabs.client import AsyncElevenLabs
from core.config import Config

logger = logging.getLogger(__name__)

class TTSService:
    def __init__(self, config: Config):
        self.api_key = config.elevenlabs_api_key
        # We only initialize the client if the key is provided
        self.client = AsyncElevenLabs(api_key=self.api_key) if self.api_key else None
        # Lily is a velvety actress voice that supports multilingual v2 (Russian, Ukrainian, English, etc.)
        self.default_voice = "pFZP5JQG7iQjIQuC4Bku" 
        self.model_id = "eleven_multilingual_v2"

    @property
    def is_configured(self) -> bool:
        return bool(self.client)

    async def generate_voice(self, text: str) -> Optional[bytes]:
        """Generate voice audio bytes from text using ElevenLabs."""
        if not self.is_configured:
            raise ValueError("ElevenLabs API key is not configured.")

        try:
            # convert() returns an AsyncIterator[bytes] (no await when calling it)
            audio_generator = self.client.text_to_speech.convert(
                voice_id=self.default_voice,
                text=text,
                model_id=self.model_id
            )
            
            # Collect the bytes from the async generator
            audio_bytes = b""
            async for chunk in audio_generator:
                audio_bytes += chunk
                
            return audio_bytes
        except Exception as e:
            logger.error(f"Failed to generate TTS: {e}")
            raise Exception(f"TTS API Error: {str(e)}")

# Global instance
_tts_service_instance = None

def get_tts_service() -> TTSService:
    global _tts_service_instance
    if _tts_service_instance is None:
        _tts_service_instance = TTSService(Config())
    return _tts_service_instance
