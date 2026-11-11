import os
import logging
import asyncio
from typing import Optional, List, Dict, Any, Tuple
import yt_dlp

logger = logging.getLogger(__name__)

class MusicService:
    CACHE_DIR = "/tmp/music_cache"

    def __init__(self):
        os.makedirs(self.CACHE_DIR, exist_ok=True)

    async def search_music(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._search_sync, query, limit)

    def _search_sync(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'skip_download': True,
            'no_warnings': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                res = ydl.extract_info(f'scsearch{limit}:{query}', download=False)
                tracks = []
                for e in res.get('entries', []):
                    if not e or not e.get('id'):
                        continue
                    duration_s = e.get('duration') or 0
                    dur_str = f"{int(duration_s // 60)}:{int(duration_s % 60):02d}" if duration_s else "unknown"
                    tracks.append({
                        'track_id': str(e.get('id')),
                        'title': e.get('title') or query,
                        'uploader': e.get('uploader') or e.get('artist') or 'Unknown Artist',
                        'duration': dur_str
                    })
                return tracks
            except Exception as e:
                logger.error(f"Failed to search music for '{query}': {e}")
                return []

    async def download_track(self, track_id: Optional[str] = None, query: Optional[str] = None) -> Tuple[Optional[str], str, str]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._download_sync, track_id, query)

    def _download_sync(self, track_id: Optional[str] = None, query: Optional[str] = None) -> Tuple[Optional[str], str, str]:
        os.makedirs(self.CACHE_DIR, exist_ok=True)
        outtmpl = os.path.join(self.CACHE_DIR, "%(id)s.%(ext)s")
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': outtmpl,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
            'no_warnings': True,
        }
        targets = []
        if track_id:
            clean_id = str(track_id).strip()
            if clean_id.startswith("http"):
                targets.append(clean_id)
            else:
                targets.append(f"https://api.soundcloud.com/tracks/{clean_id}")
        if query:
            try:
                with yt_dlp.YoutubeDL({'quiet': True, 'extract_flat': True, 'skip_download': True, 'no_warnings': True}) as ydl:
                    res = ydl.extract_info(f'scsearch5:{query}', download=False)
                    for e in res.get('entries', []):
                        if e and e.get('id'):
                            targets.append(f"https://api.soundcloud.com/tracks/{e['id']}")
            except Exception as q_err:
                logger.warning(f"Failed to query SoundCloud for download targets: {q_err}")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for t in targets:
                try:
                    info = ydl.extract_info(t, download=True)
                    entry = info['entries'][0] if 'entries' in info else info
                    tid = entry.get('id')
                    title = entry.get('title') or query or "Music Track"
                    uploader = entry.get('uploader') or entry.get('artist') or "Mia Music"
                    mp3_path = os.path.join(self.CACHE_DIR, f"{tid}.mp3")
                    if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
                        return mp3_path, title, uploader
                except Exception as dl_err:
                    logger.debug(f"Target {t} download failed, trying next: {dl_err}")
                    continue

        return None, "", ""

_music_service_instance = None

def get_music_service() -> MusicService:
    global _music_service_instance
    if _music_service_instance is None:
        _music_service_instance = MusicService()
    return _music_service_instance
