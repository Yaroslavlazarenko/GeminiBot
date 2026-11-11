import os
import logging
import asyncio
from typing import Optional, List, Dict, Any, Tuple
import yt_dlp

logger = logging.getLogger(__name__)

class MusicService:
    CACHE_DIR = "/tmp/music_cache"
    COOKIE_PATHS = [
        "/app/data/cookies.txt",
        "/tmp/cookies.txt",
        "/home/ubuntu/UtilityHubAPI/cookies.txt"
    ]

    def __init__(self):
        os.makedirs(self.CACHE_DIR, exist_ok=True)

    def _get_cookie_file(self) -> Optional[str]:
        for p in self.COOKIE_PATHS:
            if os.path.exists(p) and os.path.getsize(p) > 0:
                return p
        return None

    def _get_base_ydl_opts(self) -> dict:
        cookie_file = self._get_cookie_file()
        opts = {
            'quiet': True,
            'no_warnings': True,
            'remote_components': {'ejs:github': True},
        }
        if os.path.exists('/usr/bin/node'):
            opts['js_runtimes'] = {'node': {'path': '/usr/bin/node'}}
        if cookie_file:
            opts['cookiefile'] = cookie_file
        return opts

    async def search_music(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._search_sync, query, limit)

    def _search_sync(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        opts = self._get_base_ydl_opts()
        opts['extract_flat'] = True
        opts['skip_download'] = True

        tracks = []
        # 1. Try YouTube Search first
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                res = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
                for e in res.get("entries", []):
                    if not e or not e.get("id"):
                        continue
                    dur = e.get("duration") or 0
                    if 0 < dur <= 45:
                        continue  # Skip short snippets
                    dur_str = f"{int(dur // 60)}:{int(dur % 60):02d}" if dur else "unknown"
                    tracks.append({
                        "track_id": f"yt_{e['id']}",
                        "title": e.get("title") or query,
                        "uploader": e.get("uploader") or e.get("channel") or "YouTube",
                        "duration": dur_str
                    })
        except Exception as yt_err:
            logger.warning(f"YouTube search error for '{query}': {yt_err}")

        # 2. Fallback to SoundCloud Search (filtering out 30s snippets)
        if not tracks:
            try:
                sc_opts = dict(opts)
                with yt_dlp.YoutubeDL(sc_opts) as ydl:
                    res = ydl.extract_info(f"scsearch{limit * 2}:{query}", download=False)
                    for e in res.get("entries", []):
                        if not e or not e.get("id"):
                            continue
                        dur = e.get("duration") or 0
                        if 0 < dur <= 45:
                            continue  # Skip SoundCloud 30s preview snippets!
                        dur_str = f"{int(dur // 60)}:{int(dur % 60):02d}" if dur else "unknown"
                        tracks.append({
                            "track_id": f"sc_{e['id']}",
                            "title": e.get("title") or query,
                            "uploader": e.get("uploader") or e.get("artist") or "SoundCloud",
                            "duration": dur_str
                        })
                        if len(tracks) >= limit:
                            break
            except Exception as sc_err:
                logger.warning(f"SoundCloud search error for '{query}': {sc_err}")

        return tracks

    async def download_track(self, track_id: Optional[str] = None, query: Optional[str] = None) -> Tuple[Optional[str], str, str]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._download_sync, track_id, query)

    def _download_sync(self, track_id: Optional[str] = None, query: Optional[str] = None) -> Tuple[Optional[str], str, str]:
        os.makedirs(self.CACHE_DIR, exist_ok=True)
        outtmpl = os.path.join(self.CACHE_DIR, "%(id)s.%(ext)s")
        opts = self._get_base_ydl_opts()
        opts.update({
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': outtmpl,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })

        targets = []
        if track_id:
            tid = str(track_id).strip()
            if tid.startswith("http"):
                targets.append(tid)
            elif tid.startswith("yt_"):
                targets.append(f"https://www.youtube.com/watch?v={tid[3:]}")
            elif tid.startswith("sc_"):
                targets.append(f"https://api.soundcloud.com/tracks/{tid[3:]}")
            elif len(tid) == 11 and not tid.isdigit():
                targets.append(f"https://www.youtube.com/watch?v={tid}")
            else:
                targets.append(f"https://api.soundcloud.com/tracks/{tid}")

        if query:
            targets.append(f"ytsearch1:{query}")
            targets.append(f"scsearch5:{query}")

        with yt_dlp.YoutubeDL(opts) as ydl:
            for t in targets:
                try:
                    info = ydl.extract_info(t, download=True)
                    entry = info['entries'][0] if ('entries' in info and info['entries']) else info
                    dur = entry.get('duration') or 0
                    if 0 < dur <= 45 and 'scsearch' in t:
                        logger.debug("Skipping SoundCloud 30s snippet")
                        continue
                    out_id = entry.get('id')
                    title = entry.get('title') or query or "Music Track"
                    uploader = entry.get('uploader') or entry.get('artist') or "Music"
                    mp3_path = os.path.join(self.CACHE_DIR, f"{out_id}.mp3")
                    if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 100000:
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
