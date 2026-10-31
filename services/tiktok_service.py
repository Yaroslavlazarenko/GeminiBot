import logging
import asyncio
import os
import re
import uuid
import json
import base64
import subprocess
import urllib.request
from typing import Optional, Dict, Any
from core.config import Config

logger = logging.getLogger(__name__)

class TikTokService:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.base_url = (self.config.gemini_base_url or "http://172.17.0.1:8080").rstrip("/")
        self.api_key = self.config.gemini_api_key or "346c3424-e07f-469e-bdea-19b3f1f3168f"
        self.ai_model = "gemini-fast"

    def _analyze_sync(self, url: str) -> Optional[Dict[str, Any]]:
        clean_url = url.strip()
        api_url = "https://www.tikwm.com/api/?url=" + clean_url
        
        req = urllib.request.Request(api_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                res = json.loads(r.read().decode())
        except Exception as net_err:
            logger.warning(f"TikWM request failed: {net_err}")
            return None

        if res.get("code") != 0 or "data" not in res:
            logger.warning(f"TikWM returned non-zero code: {res.get('msg')}")
            return None

        d = res["data"]
        title = d.get("title", "")
        author = d.get("author", {}).get("nickname", "TikTok User")
        duration = d.get("duration", 0)
        images = d.get("images", [])

        parts = []

        # 1. Photo Slideshow Mode
        if images and len(images) > 0:
            logger.info(f"Processing TikTok photo slideshow with {len(images)} slides...")
            for img_url in images[:6]:
                try:
                    img_req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(img_req, timeout=8) as ir:
                        b64 = base64.b64encode(ir.read()).decode("utf-8")
                        parts.append({"inlineData": {"mimeType": "image/jpeg", "data": b64}})
                except Exception as img_err:
                    logger.warning(f"Failed to fetch slideshow slide: {img_err}")

            if not parts:
                return None

            prompt_text = (
                f"Это слайдшоу/карусель фотографий из TikTok с описанием: '{title}'. "
                f"Кратко опиши содержание каждого слайда и объясни общий сюжет, мем или суть поста на русском языке."
            )
            parts.append({"text": prompt_text})

        # 2. Standard Video Mode
        else:
            play_url = d.get("play")
            if not play_url:
                logger.warning("No play URL in TikTok data")
                return None

            temp_id = str(uuid.uuid4())[:8]
            raw_path = f"/tmp/tk_{temp_id}_raw.mp4"
            comp_path = f"/tmp/tk_{temp_id}_comp.mp4"

            try:
                logger.info(f"Downloading TikTok video for analysis: {title[:50]}...")
                dl_req = urllib.request.Request(play_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(dl_req, timeout=15) as vr, open(raw_path, "wb") as f_out:
                    f_out.write(vr.read())

                # Compress to 360p lightweight video for fast multimodal upload
                cmd = [
                    "/usr/local/bin/ffmpeg", "-y", "-i", raw_path,
                    "-vf", "scale=-2:360", "-c:v", "libx264", "-crf", "32",
                    "-c:a", "aac", "-b:a", "48k", comp_path
                ]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)

                if not os.path.exists(comp_path) or os.path.getsize(comp_path) == 0:
                    target_file = raw_path
                else:
                    target_file = comp_path

                with open(target_file, "rb") as vf:
                    v_b64 = base64.b64encode(vf.read()).decode("utf-8")

                parts.append({"inlineData": {"mimeType": "video/mp4", "data": v_b64}})
                prompt_text = (
                    f"Это видео из TikTok с названием: '{title}'. "
                    f"Лаконично (в 2-3 предложениях) опиши на русском языке, что происходит в видео, кто в кадре, "
                    f"что говорят или написано на экране, и в чём заключается юмор, твист или главный посыл ролика."
                )
                parts.append({"text": prompt_text})
            except Exception as vid_err:
                logger.error(f"Error downloading/compressing TikTok video: {vid_err}")
                return None
            finally:
                if os.path.exists(raw_path):
                    try: os.remove(raw_path)
                    except: pass
                if os.path.exists(comp_path):
                    try: os.remove(comp_path)
                    except: pass

        # 3. Query Gemini for video understanding
        try:
            url_proxy = f"{self.base_url}/v1beta/models/{self.ai_model}:generateContent"
            req_ai = urllib.request.Request(
                url_proxy,
                data=json.dumps({"contents": [{"parts": parts}]}).encode(),
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self.api_key,
                    "x-api-key": self.api_key
                }
            )
            with urllib.request.urlopen(req_ai, timeout=35) as r_ai:
                ai_res = json.loads(r_ai.read().decode())
                desc = ai_res["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as ai_err:
            logger.error(f"Gemini TikTok video understanding error: {ai_err}")
            desc = f"Название видео: {title}. (Детальный видеоанализ временно недоступен)."

        return {
            "author": author,
            "title": title,
            "duration": duration,
            "description": desc.strip(),
            "url": clean_url
        }

    async def extract_and_analyze(self, url: str) -> Optional[Dict[str, Any]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._analyze_sync, url)

_tiktok_service = None

def get_tiktok_service() -> TikTokService:
    global _tiktok_service
    if _tiktok_service is None:
        _tiktok_service = TikTokService()
    return _tiktok_service
