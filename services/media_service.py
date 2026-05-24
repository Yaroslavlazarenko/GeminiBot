import io
import logging
from aiogram import Bot
from aiogram.types import Message
from typing import Optional, Tuple
from PIL import Image

logger = logging.getLogger(__name__)

# 4.5 MB in bytes
MAX_MEDIA_SIZE_BYTES = 4.5 * 1024 * 1024

class MediaService:
    @staticmethod
    async def process_image(bot: Bot, file_id: str, file_size: int) -> Optional[bytes]:
        """Download an image and resize it optimally to fit within the 4.5MB context limit."""
        try:
            # We don't download blindly. Max 15MB for initial download.
            if file_size and file_size > 15 * 1024 * 1024:
                 logger.warning(f"Image too large to even attempt processing: {file_size} bytes")
                 return None

            file = await bot.get_file(file_id)
            file_path = file.file_path
            
            # Download file to memory
            downloaded_bytes = io.BytesIO()
            await bot.download_file(file_path, destination=downloaded_bytes)
            downloaded_bytes.seek(0)
            
            # Process with Pillow
            original_img = Image.open(downloaded_bytes)
            
            # Convert to RGB if necessary (e.g. RGBA pngs)
            if original_img.mode in ("RGBA", "P"):
                original_img = original_img.convert("RGB")
            
            # Try resolutions from highest (2K) down to 720p until it fits under 4.5MB
            resolutions = [(2560, 1440), (1920, 1080), (1280, 720)]
            
            for res in resolutions:
                img = original_img.copy()
                img.thumbnail(res, Image.Resampling.LANCZOS)
                
                output_bytes = io.BytesIO()
                img.save(output_bytes, format="JPEG", quality=85)
                result_bytes = output_bytes.getvalue()
                
                if len(result_bytes) <= MAX_MEDIA_SIZE_BYTES:
                    return result_bytes
                    
            logger.warning("Image still too large after maximum compression.")
            return None
            
        except Exception as e:
            logger.error(f"Failed to process image: {e}")
            return None

    @staticmethod
    async def process_audio_video(bot: Bot, file_id: str, file_size: int) -> Optional[bytes]:
        """Download audio/video if it's under the 4.5 MB limit. Does not compress."""
        if file_size > MAX_MEDIA_SIZE_BYTES:
            logger.warning(f"Audio/Video exceeds 4.5MB limit ({file_size} bytes). Rejected.")
            return None
            
        try:
            file = await bot.get_file(file_id)
            file_path = file.file_path
            
            downloaded_bytes = io.BytesIO()
            await bot.download_file(file_path, destination=downloaded_bytes)
            
            return downloaded_bytes.getvalue()
        except Exception as e:
            logger.error(f"Failed to download audio/video: {e}")
            return None
