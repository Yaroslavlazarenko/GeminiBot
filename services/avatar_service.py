import logging
import asyncio
from datetime import datetime
from aiogram import Bot
from google.genai.types import Part, GenerateContentConfig, Content
from core.database import DatabaseManager
from core.config import Config
from core.key_manager import get_key_manager
from services.media_service import MediaService

logger = logging.getLogger(__name__)

class AvatarService:
    _bot_avatar_cache: dict = {}  # {file_unique_id: description}

    @staticmethod
    async def get_bot_avatar_description(bot: Bot, db_manager: DatabaseManager) -> str:
        """Fetch and describe the bot's own current profile photo using Gemini Vision."""
        try:
            me = await bot.get_me()
            photos = await bot.get_user_profile_photos(me.id, limit=1)
            if not photos or not photos.photos:
                return "У тебя (Мия) пока нет установленной аватарки в профиле."

            latest_photo = photos.photos[0][-1]
            file_unique_id = latest_photo.file_unique_id
            if file_unique_id in AvatarService._bot_avatar_cache:
                return AvatarService._bot_avatar_cache[file_unique_id]

            # Check DB cache
            settings = await db_manager.get_system_settings()
            cached_bot_id = settings.get("bot_avatar_file_unique_id")
            cached_bot_desc = settings.get("bot_avatar_description")
            if cached_bot_id == file_unique_id and cached_bot_desc:
                AvatarService._bot_avatar_cache[file_unique_id] = cached_bot_desc
                return cached_bot_desc

            # Download and describe
            avatar_bytes = await MediaService.process_image(bot, latest_photo.file_id, latest_photo.file_size)
            if not avatar_bytes:
                return "Твоя аватарка установлена, но не удалось загрузить её файл для анализа."

            from core.key_manager import get_key_manager
            key_manager = get_key_manager()
            config = Config()
            vision_model = settings.get("gemini_api_model") or config.gemini_api_model or "gemini-3.5-flash"

            prompt = (
                "Ты — Миа Зарева (Mia Zareva), 22 года, из Одессы. Это ТВОЯ СОБСТВЕННАЯ аватарка профиля в Telegram. "
                "Опиши кратко и естественно от первого лица (что на ней изображено, в каком стиле, как ты выглядишь, прическа, одежда, вайб). "
                "Сделай это в 2-3 предложениях, без формализма, чтобы ты сама всегда помнила, как выглядишь на своей аватарке."
            )
            contents = [
                Content(role="user", parts=[
                    Part.from_text(text=prompt),
                    Part.from_bytes(data=avatar_bytes, mime_type="image/jpeg")
                ])
            ]
            resp = key_manager.generate_content(
                model=vision_model,
                contents=contents,
                config=GenerateContentConfig(temperature=0.3)
            )
            desc = resp.text.strip() if resp and resp.text else "Реалистичное зеркальное селфи девушки в гранж/альт стиле с темными волосами."
            AvatarService._bot_avatar_cache[file_unique_id] = desc

            await db_manager.system_settings.update_one(
                {},
                {"$set": {
                    "bot_avatar_file_unique_id": file_unique_id,
                    "bot_avatar_description": desc,
                    "bot_avatar_updated_at": datetime.utcnow()
                }},
                upsert=True
            )
            logger.info(f"Generated new bot avatar description: {desc[:60]}...")
            return desc
        except Exception as e:
            logger.error(f"Failed to describe bot avatar: {e}")
            return "Реалистичное зеркальное селфи девушки в гранж/альт стиле с темными волосами."

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
