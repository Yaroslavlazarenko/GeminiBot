import logging
import asyncio
import re
import json
import base64
from typing import Optional
import httpx
import edge_tts
from core.config import Config

logger = logging.getLogger(__name__)

class TTSService:
    VOICE_RU = "ru-RU-SvetlanaNeural"
    VOICE_UA = "uk-UA-PolinaNeural"
    VOICE_EN = "en-US-JennyNeural"
    VOICE_DE = "de-DE-KatjaNeural"

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.base_url = (self.config.gemini_base_url or "http://172.17.0.1:8080").rstrip("/")
        self.api_key = self.config.gemini_api_key or "346c3424-e07f-469e-bdea-19b3f1f3168f"

    @property
    def is_configured(self) -> bool:
        return True

    def detect_voice(self, text: str, language: str = "auto") -> str:
        """Detect or map to the appropriate EdgeTTS voice based on language code or text analysis."""
        lang = (language or "auto").lower().strip()
        if lang in ("en", "eng", "english"):
            return self.VOICE_EN
        if lang in ("uk", "ua", "ukrainian"):
            return self.VOICE_UA
        if lang in ("de", "ger", "german", "deutsch"):
            return self.VOICE_DE
        if lang in ("ru", "rus", "russian"):
            return self.VOICE_RU

        # Automatic detection based on characters and script
        latin_count = len(re.findall(r"[a-zA-Z]", text))
        cyrillic_count = len(re.findall(r"[а-яёА-ЯЁіІїЇєЄґҐ]", text))

        if latin_count > cyrillic_count:
            de_markers = re.findall(r"[äöüßÄÖÜ]", text)
            de_common_words = {"der", "die", "das", "und", "ich", "nicht", "hallo", "guten", "morgen", "tag", "wie", "geht", "es"}
            words = set(re.findall(r"\b[a-zA-ZäöüßÄÖÜ]+\b", text.lower()))
            if de_markers or (words & de_common_words):
                return self.VOICE_DE
            return self.VOICE_EN

        # Cyrillic: check for Ukrainian specific letters
        ua_markers = {"і", "ї", "є", "ґ"}
        if any(ch in text.lower() for ch in ua_markers):
            return self.VOICE_UA
        return self.VOICE_RU

    async def generate_voice(self, text: str, language: str = "auto", voice: Optional[str] = None) -> Optional[bytes]:
        """
        Generate voice audio bytes in Telegram native OGG/OPUS format.
        First tries Universal Gateway (gemini-audio), then falls back to Edge-TTS.
        """
        clean_text = text.strip()
        if not clean_text:
            return None

        # 1. Try generating through Universal Gateway (gemini-audio)
        try:
            pcm_bytes = await self._generate_gateway_audio(clean_text, voice=voice)
            if pcm_bytes:
                logger.info(f"✅ Received Gemini TTS audio from Gateway ({len(pcm_bytes)} bytes), converting to OGG...")
                ogg_bytes = await self._pcm_to_ogg(pcm_bytes, rate=24000)
                if ogg_bytes:
                    return ogg_bytes
        except Exception as e:
            logger.warning(f"⚠️ Gateway TTS failed, falling back to local Edge-TTS: {e}")

        # 2. Fallback to local EdgeTTS
        return await self._generate_edge_tts_ogg(clean_text, language=language, voice=voice)

    async def _generate_gateway_audio(self, text: str, voice: Optional[str] = None) -> Optional[bytes]:
        url = f"{self.base_url}/v1beta/models/gemini-audio:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
            "x-api-key": self.api_key
        }

        body: dict = {
            "contents": [{"parts": [{"text": text}]}]
        }
        if voice:
            body["speechConfig"] = {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice
                    }
                }
            }

        async with httpx.AsyncClient(timeout=35.0) as client:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code != 200:
                raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise Exception("No candidates returned from gemini-audio")

            parts = candidates[0].get("content", {}).get("parts", [])
            for p in parts:
                if "inlineData" in p and "data" in p["inlineData"]:
                    b64_data = p["inlineData"]["data"]
                    return base64.b64decode(b64_data)

        return None

    async def _pcm_to_ogg(self, pcm_bytes: bytes, rate: int = 24000) -> Optional[bytes]:
        """Convert 16-bit linear PCM mono into Telegram native OGG (Opus codec)."""
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
            "-c:a", "libopus", "-b:a", "32k", "-vbr", "on", "-f", "ogg", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        ogg_bytes, err = await proc.communicate(input=pcm_bytes)
        if proc.returncode != 0 or not ogg_bytes:
            logger.warning(f"ffmpeg PCM->OGG conversion failed ({proc.returncode}): {err.decode() if err else ''}")
            return None
        return ogg_bytes

    async def _generate_edge_tts_ogg(self, text: str, language: str = "auto", voice: Optional[str] = None) -> Optional[bytes]:
        selected_voice = voice or self.detect_voice(text, language=language)
        logger.info(f"Generating EdgeTTS voice ({selected_voice}) for text: {text[:50]}...")

        comm = edge_tts.Communicate(text, voice=selected_voice)
        mp3_bytes = b""
        async for chunk in comm.stream():
            if chunk.get("type") == "audio" and "data" in chunk:
                mp3_bytes += chunk["data"]

        if not mp3_bytes:
            raise Exception("EdgeTTS returned empty audio stream.")

        # Convert MP3 to Telegram native OGG (Opus codec)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "32k", "-vbr", "on", "-f", "ogg", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        ogg_bytes, err = await proc.communicate(input=mp3_bytes)
        if proc.returncode != 0 or not ogg_bytes:
            logger.warning(f"ffmpeg MP3->OGG conversion failed ({proc.returncode}), using raw MP3: {err.decode() if err else ''}")
            return mp3_bytes

        return ogg_bytes

# Global instance
_tts_service_instance = None

def get_tts_service() -> TTSService:
    global _tts_service_instance
    if _tts_service_instance is None:
        _tts_service_instance = TTSService(Config())
    return _tts_service_instance
