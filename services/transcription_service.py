import logging
import aiohttp
from typing import Optional
from core.config import Config

logger = logging.getLogger(__name__)

class TranscriptionService:
    def __init__(self, config: Config):
        self.api_key = config.groq_api_key
        self.api_url = "https://api.groq.com/openai/v1/audio/transcriptions"
        self.model = "whisper-large-v3"

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def transcribe_audio(self, audio_bytes: bytes) -> Optional[str]:
        """Transcribe audio bytes to text using Groq's whisper-large-v3 model."""
        if not self.is_configured:
            logger.warning("Transcription skipped: Groq API key is not configured.")
            return None

        try:
            data = aiohttp.FormData()
            data.add_field(
                'file',
                audio_bytes,
                filename='voice.ogg',
                content_type='audio/ogg'
            )
            data.add_field('model', self.model)

            headers = {
                "Authorization": f"Bearer {self.api_key}"
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(self.api_url, headers=headers, data=data) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Groq API error (status {response.status}): {error_text}")
                        return None
                    
                    result = await response.json()
                    return result.get("text", "")
        except Exception as e:
            logger.error(f"Failed to transcribe audio via Groq: {e}", exc_info=True)
            return None

# Global instance
_transcription_service_instance = None

def get_transcription_service() -> TranscriptionService:
    global _transcription_service_instance
    if _transcription_service_instance is None:
        _transcription_service_instance = TranscriptionService(Config())
    return _transcription_service_instance
