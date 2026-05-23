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
        # Rachel is a great, natural, young female voice available on the free tier
        # It supports multilingual v2 (Russian, Ukrainian, English, etc.)
        self.default_voice = "Rachel" 
        self.model_id = "eleven_multilingual_v2"

    @property
    def is_configured(self) -> bool:
        return bool(self.client)

    async def generate_voice(self, text: str) -> Optional[bytes]:
        """Generate voice audio bytes from text using ElevenLabs."""
        if not self.is_configured:
            logger.warning("TTS generation skipped: ElevenLabs API key is not configured.")
            return None

        try:
            # generate() returns an async generator of bytes
            audio_generator = await self.client.generate(
                text=text,
                voice=self.default_voice,
                model=self.model_id
            )
            
            # Collect the bytes from the async generator
            audio_bytes = b""
            async for chunk in audio_generator:
                audio_bytes += chunk
                
            return audio_bytes
        except Exception as e:
            logger.error(f"Failed to generate TTS: {e}")
            return None

# Global instance
_tts_service_instance = None

def get_tts_service() -> TTSService:
    global _tts_service_instance
    if _tts_service_instance is None:
        _tts_service_instance = TTSService(Config())
    return _tts_service_instance
