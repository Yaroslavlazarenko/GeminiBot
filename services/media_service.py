import io
import os
import logging
import asyncio
import tempfile
from aiogram import Bot
from aiogram.types import Message
from typing import Optional, Tuple
from PIL import Image

logger = logging.getLogger(__name__)

# 4.5 MB in bytes
MAX_MEDIA_SIZE_BYTES = 4.5 * 1024 * 1024
# Max video size we'll attempt to download for audio extraction (20 MB via Bot API limit)
MAX_VIDEO_DOWNLOAD_FOR_AUDIO = 20 * 1024 * 1024

class MediaService:
    @staticmethod
    def get_max_media_size() -> int:
        """Return the max media size in bytes for Gemini context."""
        return int(MAX_MEDIA_SIZE_BYTES)

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

    @staticmethod
    async def extract_audio_from_video(bot: Bot, file_id: str, file_size: int) -> Optional[bytes]:
        """
        Download a large video and extract its audio track via ffmpeg.
        Returns OGG audio bytes, or None on failure.
        Used when the video is too large for Gemini but we still want the audio.
        """
        if file_size > MAX_VIDEO_DOWNLOAD_FOR_AUDIO:
            logger.warning(f"Video too large even for audio extraction ({file_size} bytes). Skipping.")
            return None

        tmp_video = None
        tmp_audio = None
        try:
            file = await bot.get_file(file_id)
            file_path = file.file_path

            # Download video to a temp file
            tmp_video = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            downloaded_bytes = io.BytesIO()
            await bot.download_file(file_path, destination=downloaded_bytes)
            tmp_video.write(downloaded_bytes.getvalue())
            tmp_video.close()

            # Extract audio with ffmpeg
            tmp_audio_path = tmp_video.name.replace(".mp4", ".ogg")
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-i", tmp_video.name,
                "-vn",                # no video
                "-acodec", "libopus", # opus codec for ogg
                "-b:a", "64k",        # 64kbps — small but intelligible
                "-y",                 # overwrite
                tmp_audio_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=120)

            if process.returncode != 0:
                err_msg = stderr.decode(errors="replace")[-500:] if stderr else "unknown"
                logger.error(f"ffmpeg audio extraction failed (rc={process.returncode}): {err_msg}")
                return None

            if not os.path.exists(tmp_audio_path):
                logger.error("ffmpeg produced no output file")
                return None

            with open(tmp_audio_path, "rb") as f:
                audio_bytes = f.read()

            # Clean up audio temp file
            os.unlink(tmp_audio_path)

            if len(audio_bytes) == 0:
                logger.warning("Extracted audio is empty — video likely has no audio track")
                return None

            logger.info(f"Extracted audio from video: {len(audio_bytes)} bytes")
            return audio_bytes

        except asyncio.TimeoutError:
            logger.error("ffmpeg audio extraction timed out")
            return None
        except Exception as e:
            logger.error(f"Failed to extract audio from video: {e}", exc_info=True)
            return None
        finally:
            # Clean up temp video file
            if tmp_video and os.path.exists(tmp_video.name):
                try:
                    os.unlink(tmp_video.name)
                except Exception:
                    pass
