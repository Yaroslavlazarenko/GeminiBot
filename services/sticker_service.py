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
        """Downloads missing stickers, sends them to Gemini for description, and caches them. Also cleans up removed packs."""
        logger.info(f"Starting background sync for sticker packs: {pack_names}")
        
        # Cleanup removed packs (Keep 'user_discovered' safe)
        if pack_names:
            await db.stickers.delete_many({"pack_name": {"$nin": pack_names, "$ne": "user_discovered"}})
        else:
            await db.stickers.delete_many({"pack_name": {"$ne": "user_discovered"}})
        
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
                
                # Dynamic Batch processing to respect 3.5MB and token limits
                current_batch = []
                current_size = 0
                max_size = 3.2 * 1024 * 1024 # 3.2 MB safety limit
                max_items = 12
                
                for sticker in missing_stickers:
                    file_id = sticker.file_id
                    if sticker.is_animated:
                        if sticker.thumbnail:
                            file_id = sticker.thumbnail.file_id
                        else:
                            await db.stickers.insert_one({
                                "_id": sticker.file_unique_id,
                                "file_id": sticker.file_id,
                                "pack_name": pack_name,
                                "emoji": sticker.emoji,
                                "description": f"[{sticker.emoji}] Animated sticker (No description available)"
                            })
                            continue
                            
                    try:
                        file = await bot.get_file(file_id)
                        size = file.file_size
                        
                        if current_size + size > max_size or len(current_batch) >= max_items:
                            # Process current batch
                            await StickerService._process_batch(bot, db, key_manager, pack_name, current_batch)
                            current_batch = []
                            current_size = 0
                            await asyncio.sleep(2.5) # Prevent rate limits
                            
                        current_batch.append((sticker, file))
                        current_size += size
                    except Exception as e:
                        logger.error(f"Error fetching file info for sticker {sticker.file_unique_id}: {e}")
                        
                # Process remaining
                if current_batch:
                    await StickerService._process_batch(bot, db, key_manager, pack_name, current_batch)
                    
                logger.info(f"Finished analyzing pack '{pack_name}'.")
                    
            except Exception as e:
                logger.error(f"Error syncing pack {pack_name}: {e}")

    @staticmethod
    async def analyze_single_sticker(bot: Bot, db: DatabaseManager, key_manager: GeminiKeyManager, sticker) -> str:
        """Analyzes a single user-sent sticker on the fly."""
        existing = await db.stickers.find_one({"_id": sticker.file_unique_id})
        if existing:
            return existing.get("description", f"[{sticker.emoji}] Sticker")
            
        logger.info(f"Analyzing new user-sent sticker: {sticker.file_unique_id}")
        
        file_id = sticker.file_id
        if sticker.is_animated:
            if sticker.thumbnail:
                file_id = sticker.thumbnail.file_id
            else:
                desc = f"[{sticker.emoji}] Animated sticker (No description available)"
                await db.stickers.insert_one({
                    "_id": sticker.file_unique_id,
                    "file_id": sticker.file_id,
                    "pack_name": "user_discovered",
                    "emoji": sticker.emoji,
                    "description": desc
                })
                return desc
                
        try:
            file = await bot.get_file(file_id)
            if file.file_size > 3.5 * 1024 * 1024:
                return f"[{sticker.emoji}] Sticker (Too large to analyze)"
                
            downloaded_bytes = io.BytesIO()
            await bot.download_file(file.file_path, destination=downloaded_bytes)
            img_data = downloaded_bytes.getvalue()
            
            if sticker.is_video:
                mime_type = "video/webm"
            else:
                mime_type = "image/webp" if file.file_path.endswith('.webp') else "image/jpeg"
            
            prompt_text = (
                "Provide a brief 1-sentence visual description of this character/sticker (image or short video) and what emotion they are expressing. "
                "Keep it concise. E.g., 'A white cat wearing sunglasses, looking cool.'"
            )
            
            settings = await db.get_system_settings()
            model_name = settings.get("gemini_api_model") or "gemini-3.5-flash"
            
            response = key_manager.generate_content(
                model=model_name,
                contents=[prompt_text, Part.from_bytes(data=img_data, mime_type=mime_type)],
                config=GenerateContentConfig(temperature=0.2)
            )
            
            desc = response.text.strip() if response.text else f"[{sticker.emoji}] Sticker"
            
            # Save it permanently
            await db.stickers.insert_one({
                "_id": sticker.file_unique_id,
                "file_id": sticker.file_id,
                "pack_name": "user_discovered",
                "emoji": sticker.emoji,
                "description": desc
            })
            
            return desc
        except Exception as e:
            logger.error(f"Error analyzing user sticker {sticker.file_unique_id}: {e}")
            return f"[{sticker.emoji}] Sticker"

    @staticmethod
    async def analyze_video_note(bot: Bot, key_manager: GeminiKeyManager, file_id: str) -> str:
        """Analyzes a video note to generate a visual description for history context."""
        try:
            file = await bot.get_file(file_id)
            if file.file_size > 4.5 * 1024 * 1024:
                return "(Video note too large to analyze)"
                
            downloaded_bytes = io.BytesIO()
            await bot.download_file(file.file_path, destination=downloaded_bytes)
            img_data = downloaded_bytes.getvalue()
            
            prompt_text = (
                "Provide a brief 1-sentence visual description of what you see in this round video message (video note). "
                "Describe the person, their facial expression, surroundings, or what they are doing. Ignore audio."
            )
            
            response = key_manager.generate_content(
                model="gemini-3.5-flash",
                contents=[prompt_text, Part.from_bytes(data=img_data, mime_type="video/mp4")],
                config=GenerateContentConfig(temperature=0.2)
            )
            
            return response.text.strip() if response.text else "(No visual description available)"
        except Exception as e:
            logger.error(f"Error analyzing video note {file_id}: {e}")
            return "(Video note analysis failed)"

    @staticmethod
    async def _process_batch(bot: Bot, db: DatabaseManager, key_manager: GeminiKeyManager, pack_name: str, batch: list):
        prompt_text = (
            "You are a helpful assistant helping a chatbot catalog its stickers. "
            "I will provide a sequence of items (images or short video clips). Before each item, I will provide its 'Sticker Index'. "
            "For each sticker, provide a brief 1-sentence visual description of what the character is doing and what emotion they are expressing. "
            "Return the descriptions in the requested JSON structure."
        )
        contents = [prompt_text]
        valid_stickers = []
        
        for idx, (sticker, file) in enumerate(batch):
            try:
                downloaded_bytes = io.BytesIO()
                await bot.download_file(file.file_path, destination=downloaded_bytes)
                img_data = downloaded_bytes.getvalue()
                
                if sticker.is_video:
                    mime_type = "video/webm"
                else:
                    mime_type = "image/webp" if file.file_path.endswith('.webp') else "image/jpeg"
                
                contents.append(f"Sticker Index: {idx}")
                contents.append(Part.from_bytes(data=img_data, mime_type=mime_type))
                
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
