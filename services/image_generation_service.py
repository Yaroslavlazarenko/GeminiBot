import logging
import asyncio
import base64
import json
from typing import Optional
import httpx
from core.config import Config

logger = logging.getLogger(__name__)

class ImageGenerationService:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.base_url = (self.config.gemini_base_url or "http://172.17.0.1:8080").rstrip("/")
        self.api_key = self.config.gemini_api_key or "346c3424-e07f-469e-bdea-19b3f1f3168f"
        self.model_name = "gemini-image"

    async def generate_image(
        self, 
        prompt: str, 
        reference_bytes: Optional[bytes | list[bytes]] = None,
        aspect_ratio: str = "auto"
    ) -> bytes:
        """Generate image using Google gemini-3.1-flash-image via internal proxy.
        Supports text-to-image, image-to-image (with reference photo), and exact aspect ratios.
        """
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise ValueError("Prompt cannot be empty for image generation.")

        # Inject framing guidance for exact pixel resolution output
        ar_lower = (aspect_ratio or "auto").lower().strip()
        prefix = ""
        if ar_lower in ("1:1", "square", "квадрат", "аватарка", "аватар"):
            if "1:1" not in clean_prompt and "square" not in clean_prompt.lower():
                prefix = "Square 1:1 aspect ratio format, 1024x1024 resolution. "
        elif ar_lower in ("9:16", "vertical", "вертикальный", "вертикальное", "обои", "сторис", "story", "stories", "wallpaper", "phone"):
            if "9:16" not in clean_prompt and "vertical" not in clean_prompt.lower():
                prefix = "Vertical 9:16 portrait format, tall smartphone wallpaper aspect ratio. "
        elif ar_lower in ("16:9", "horizontal", "widescreen", "широкий", "горизонтальный", "горизонтальное", "wide", "landscape"):
            if "16:9" not in clean_prompt and "widescreen" not in clean_prompt.lower():
                prefix = "Wide landscape 16:9 cinematic widescreen format aspect ratio. "
        elif ar_lower in ("4:3", "3:4"):
            if ar_lower not in clean_prompt:
                prefix = f"{ar_lower} aspect ratio format. "

        final_prompt = prefix + clean_prompt

        url = f"{self.base_url}/v1beta/models/{self.model_name}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
            "x-api-key": self.api_key
        }

        parts = []
        if reference_bytes:
            ref_list = reference_bytes if isinstance(reference_bytes, list) else [reference_bytes]
            for r_bytes in ref_list:
                ref_b64 = base64.b64encode(r_bytes).decode("utf-8")
                parts.append({
                    "inlineData": {
                        "mimeType": "image/jpeg",
                        "data": ref_b64
                    }
                })
            logger.info(f"Generating Image-to-Image with {len(ref_list)} reference image(s) [AR={ar_lower}]: {final_prompt[:60]}...")
        else:
            logger.info(f"Generating Text-to-Image [AR={ar_lower}]: {final_prompt[:60]}...")

        parts.append({"text": final_prompt})
        body = {"contents": [{"parts": parts}]}

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code != 200:
                error_msg = f"Image generation failed with status {resp.status_code}: {resp.text[:300]}"
                logger.error(error_msg)
                raise Exception(error_msg)

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise Exception("No candidates returned from image generation model.")

            first_parts = candidates[0].get("content", {}).get("parts", [])
            for part in first_parts:
                if "inlineData" in part and "data" in part["inlineData"]:
                    img_b64 = part["inlineData"]["data"]
                    return base64.b64decode(img_b64)

            text_resp = first_parts[0].get("text") if first_parts else "No image data in response"
            raise Exception(f"Model returned text instead of image: {text_resp[:200]}")

_image_generation_service = None

def get_image_generation_service() -> ImageGenerationService:
    global _image_generation_service
    if _image_generation_service is None:
        _image_generation_service = ImageGenerationService()
    return _image_generation_service
