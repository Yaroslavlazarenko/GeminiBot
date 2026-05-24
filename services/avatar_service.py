import logging
import asyncio
from datetime import datetime
from aiogram import Bot
from google.genai.types import Part, GenerateContentConfig
from core.database import DatabaseManager
from core.config import Config
from core.key_manager import get_key_manager
from services.media_service import MediaService

logger = logging.getLogger(__name__)

class AvatarService:
    @staticmethod
    async def get_and_describe_avatar(bot: Bot, user_id: int, db_manager: DatabaseManager) -> str:
        """
        Fetch the user's latest avatar, compare it to MongoDB cache,
        and generate/update the visual description using Gemini Vision if it has changed.
        """
        try:
            # 1. Fetch user's profile photos
            photos = await bot.get_user_profile_photos(user_id, limit=1)
            if not photos or not photos.photos:
                logger.info(f"User {user_id} has no profile photos.")
                # Update DB to reflect no avatar
                await db_manager.users.update_one(
                    {"telegram_id": user_id},
                    {"$set": {
                        "avatar_file_unique_id": "none",
                        "avatar_description": "У пользователя нет аватарки (просто стандартная заглушка Telegram).",
                        "avatar_last_checked": datetime.utcnow()
                    }}
                )
                return "У пользователя нет аватарки (просто стандартная заглушка Telegram)."

            # Get the highest resolution photo of the latest avatar
            latest_photo = photos.photos[0][-1]
            file_unique_id = latest_photo.file_unique_id
            file_id = latest_photo.file_id
            file_size = latest_photo.file_size

            # 2. Check MongoDB cache
            user_doc = await db_manager.users.find_one({"telegram_id": user_id})
            if user_doc:
                cached_id = user_doc.get("avatar_file_unique_id")
                cached_desc = user_doc.get("avatar_description")
                if cached_id == file_unique_id and cached_desc:
                    logger.debug(f"Returning cached avatar description for user {user_id}.")
                    return cached_desc

            # 3. New or changed avatar: download and describe it
            logger.info(f"Downloading and describing new avatar for user {user_id}...")
            
            # Download avatar via MediaService
            avatar_bytes = await MediaService.process_image(bot, file_id, file_size)
            if not avatar_bytes:
                logger.warning(f"Could not download avatar for user {user_id}. Using fallback.")
                return "Аватарку не удалось загрузить."

            # Retrieve settings from DB
            settings = await db_manager.get_system_settings()

            key_manager = get_key_manager()

            # Query the currently active model from the DB / configuration
            config = Config()
            vision_model = settings.get("gemini_api_model") or config.gemini_api_model or "gemini-3.5-flash"
            if not vision_model:
                vision_model = "gemini-3.5-flash"

            # Query the custom prompt from the DB settings
            prompt = settings.get("avatar_prompt") or (
                "Ты — Мия Zareva (Mia Zareva), 22 года, из Одессы. Ты общаешься с пользователем в Telegram. "
                "Опиши эту аватарку пользователя в 2-3 коротких предложениях. Что на ней изображено, какой стиль, цвета, атмосфера? "
                "Пиши от первого лица (ты — Мия Zareva), как будто ты сама смотришь на неё. "
                "Будь дружелюбной и подмечай интересные детали."
            )

            # Call generate_content in a thread pool since key_manager.generate_content is synchronous
            def _call_gemini():
                return key_manager.generate_content(
                    model=vision_model,
                    contents=[
                        Part.from_bytes(
                            data=avatar_bytes,
                            mime_type="image/jpeg"
                        ),
                        prompt
                    ],
                    config=GenerateContentConfig()
                )

            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, _call_gemini)
            
            description = response.text.strip() if response.text else "Красивая аватарка, но я не могу разглядеть детали."
            logger.info(f"Generated avatar description for user {user_id}: {description}")

            # 4. Save to MongoDB cache
            await db_manager.users.update_one(
                {"telegram_id": user_id},
                {"$set": {
                    "avatar_file_unique_id": file_unique_id,
                    "avatar_description": description,
                    "avatar_last_checked": datetime.utcnow()
                }}
            )

            return description

        except Exception as e:
            logger.error(f"Error in AvatarService.get_and_describe_avatar: {e}", exc_info=True)
            return "Не удалось рассмотреть аватарку из-за технической неполадки."
