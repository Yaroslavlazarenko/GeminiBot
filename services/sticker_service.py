import asyncio
import logging
import io
import time
from typing import List
from aiogram import Bot
from pydantic import BaseModel, Field
from google.genai.types import GenerateContentConfig, Part
from core.database import DatabaseManager
from core.key_manager import GeminiKeyManager

logger = logging.getLogger(__name__)

class StickerDesc(BaseModel):
    index: int = Field(description="The index of the sticker as provided in the prompt")
    description: str = Field(description="Visual description of the sticker and its emotion. E.g. 'A white cat wearing sunglasses, looking cool.'")

class StickerBatchResult(BaseModel):
    stickers: list[StickerDesc]

class StickerService:
    @staticmethod
    async def sync_sticker_packs(bot: Bot, db: DatabaseManager, key_manager: GeminiKeyManager, pack_names: List[str]):
        """Downloads missing stickers, sends them to Gemini for description, and caches them."""
        logger.info(f"Starting background sync for sticker packs: {pack_names}")
        
        for pack_name in pack_names:
            try:
                pack = await bot.get_sticker_set(name=pack_name)
                if not pack or not pack.stickers:
                    continue
                    
                # Find which stickers are missing from DB
                missing_stickers = []
                for sticker in pack.stickers:
                    exists = await db.stickers.find_one({"_id": sticker.file_unique_id})
                    if not exists:
                        missing_stickers.append(sticker)
                        
                if not missing_stickers:
                    continue
                    
                logger.info(f"Found {len(missing_stickers)} new stickers in pack '{pack_name}'. Analyzing with Gemini Vision...")
                
                # Batch processing to respect 3.5MB and token limits (e.g. 8 at a time)
                batch_size = 8
                for i in range(0, len(missing_stickers), batch_size):
                    batch = missing_stickers[i:i+batch_size]
                    await StickerService._process_batch(bot, db, key_manager, pack_name, batch)
                    await asyncio.sleep(2) # Brief pause to prevent rate-limits
                    
                logger.info(f"Finished analyzing pack '{pack_name}'.")
                    
            except Exception as e:
                logger.error(f"Error syncing pack {pack_name}: {e}")

    @staticmethod
    async def _process_batch(bot: Bot, db: DatabaseManager, key_manager: GeminiKeyManager, pack_name: str, batch: list):
        prompt_text = (
            "You are a helpful assistant helping a chatbot catalog its stickers. "
            "I will provide a sequence of images (stickers). Before each image, I will provide its 'Sticker Index'. "
            "For each image, provide a brief 1-sentence visual description of what the character is doing and what emotion they are expressing. "
            "Return the descriptions in the requested JSON structure."
        )
        contents = [prompt_text]
        valid_stickers = []
        
        total_size = 0
        max_size = 3.5 * 1024 * 1024 # 3.5 MB limit
        
        for idx, sticker in enumerate(batch):
            try:
                # Use static representation if animated/video
                file_id = sticker.file_id
                if sticker.is_animated or sticker.is_video:
                    if sticker.thumbnail:
                        file_id = sticker.thumbnail.file_id
                    else:
                        # Fallback for animated with no thumb
                        await db.stickers.insert_one({
                            "_id": sticker.file_unique_id,
                            "file_id": sticker.file_id,
                            "pack_name": pack_name,
                            "emoji": sticker.emoji,
                            "description": f"[{sticker.emoji}] Animated/Video sticker (No description available)"
                        })
                        continue
                        
                file = await bot.get_file(file_id)
                
                if total_size + file.file_size > max_size:
                    logger.warning(f"Batch size limit reached. Processed {len(valid_stickers)} out of {len(batch)}.")
                    break
                    
                downloaded_bytes = io.BytesIO()
                await bot.download_file(file.file_path, destination=downloaded_bytes)
                img_data = downloaded_bytes.getvalue()
                
                mime_type = "image/webp" if file.file_path.endswith('.webp') else "image/jpeg"
                
                contents.append(f"Sticker Index: {idx}")
                contents.append(Part.from_bytes(data=img_data, mime_type=mime_type))
                
                total_size += file.file_size
                valid_stickers.append((idx, sticker))
                
            except Exception as e:
                logger.error(f"Error downloading sticker {sticker.file_unique_id}: {e}")

        if not valid_stickers:
            return

        try:
            settings = await db.get_system_settings()
            model_name = settings.get("gemini_api_model") or "gemini-3.5-flash"
            
            response = key_manager.generate_content(
                model=model_name,
                contents=contents,
                config=GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                    response_schema=StickerBatchResult
                )
            )
            
            result: StickerBatchResult = response.parsed
            if not result:
                import json
                data = json.loads(response.text)
                result = StickerBatchResult(**data)
            
            desc_map = {item.index: item.description for item in result.stickers}
            
            for idx, sticker in valid_stickers:
                desc = desc_map.get(idx, f"[{sticker.emoji}] Unrecognized sticker")
                
                await db.stickers.insert_one({
                    "_id": sticker.file_unique_id,
                    "file_id": sticker.file_id,
                    "pack_name": pack_name,
                    "emoji": sticker.emoji,
                    "description": desc
                })
                
        except Exception as e:
            logger.error(f"Error in Gemini Vision for stickers: {e}")
            # Fallback
            for idx, sticker in valid_stickers:
                await db.stickers.insert_one({
                    "_id": sticker.file_unique_id,
                    "file_id": sticker.file_id,
                    "pack_name": pack_name,
                    "emoji": sticker.emoji,
                    "description": f"[{sticker.emoji}] Sticker"
                })
