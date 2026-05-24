import logging
import threading
from typing import List, Optional
from google import genai
from google.genai.types import GenerateContentConfig
from core.config import Config

logger = logging.getLogger(__name__)

# HTTP status codes that should trigger key rotation
ROTATE_ON_STATUS = {429, 500, 503}
ROTATE_ON_KEYWORDS = ["quota", "rate limit", "resource exhausted", "overloaded", "unavailable"]


class GeminiKeyManager:
    """
    Thread-safe rotating Gemini API key pool.

    On any error matching ROTATE_ON_STATUS or ROTATE_ON_KEYWORDS the manager
    will cycle to the next key, rebuild the client, and retry the request.
    All retries are exhausted before raising the last exception.
    """

    def __init__(self, config: Config):
        self._lock = threading.Lock()
        self._keys: List[str] = config.get_all_api_keys()
        self._base_url: Optional[str] = config.gemini_base_url
        self._current_index: int = 0

        if not self._keys:
            raise ValueError("GeminiKeyManager: no API keys provided.")

        self._client = self._build_client(self._keys[self._current_index])
        logger.info(f"GeminiKeyManager initialised with {len(self._keys)} key(s).")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_client(self, key: str) -> genai.Client:
        http_opts = {"base_url": self._base_url} if self._base_url else None
        return genai.Client(api_key=key, http_options=http_opts)

    def _should_rotate(self, exc: Exception) -> bool:
        """Return True if this exception warrants trying the next key."""
        msg = str(exc).lower()
        # Check for known rate-limit keywords
        if any(kw in msg for kw in ROTATE_ON_KEYWORDS):
            return True
        # Check for HTTP status codes embedded in the exception string
        for code in ROTATE_ON_STATUS:
            if str(code) in msg:
                return True
        return False

    def _rotate(self) -> bool:
        """
        Advance to the next key. Returns True if a new key was selected,
        False if we have already cycled through all keys.
        """
        with self._lock:
            next_index = (self._current_index + 1) % len(self._keys)
            if next_index == 0 and len(self._keys) > 1:
                # Wrapped around — tried all keys
                logger.warning("GeminiKeyManager: exhausted all keys in pool.")
                return False
            if next_index == self._current_index:
                # Only one key — nothing to rotate to
                return False
            self._current_index = next_index
            new_key = self._keys[self._current_index]
            self._client = self._build_client(new_key)
            logger.info(
                f"GeminiKeyManager: rotated to key #{self._current_index + 1}/{len(self._keys)} "
                f"(…{new_key[-6:]})"
            )
            return True

    # ------------------------------------------------------------------
    # Public interface — drop-in replacement for client.models.generate_content
    # ------------------------------------------------------------------

    def update_base_url(self, new_base_url: Optional[str]):
        """Rebuild the client when the base URL changes (called from _sync_settings)."""
        with self._lock:
            if self._base_url != new_base_url:
                self._base_url = new_base_url
                self._client = self._build_client(self._keys[self._current_index])

    def update_settings(self, api_key: str, api_keys_str: str, base_url: Optional[str]):
        """
        Dynamically update API keys and base URL if they changed.
        Rebuilds the client transparently if needed.
        """
        keys = [api_key]
        if api_keys_str:
            for k in api_keys_str.split(","):
                k = k.strip()
                if k and k not in keys:
                    keys.append(k)
        
        # Filter out empty keys
        keys = [k for k in keys if k]
        if not keys:
            return

        with self._lock:
            changed = False
            if self._keys != keys:
                logger.info(f"GeminiKeyManager: updating keys pool ({len(self._keys)} -> {len(keys)} keys)")
                self._keys = keys
                self._current_index = min(self._current_index, len(keys) - 1)
                changed = True
            
            if self._base_url != base_url:
                logger.info(f"GeminiKeyManager: updating base URL ({self._base_url} -> {base_url})")
                self._base_url = base_url
                changed = True
                
            if changed:
                self._client = self._build_client(self._keys[self._current_index])

    def generate_content(self, model: str, contents, config: GenerateContentConfig):
        """
        Synchronous wrapper around client.models.generate_content with
        automatic key rotation on retriable errors.
        """
        last_exc = None
        attempts = len(self._keys)  # try each key at most once per call

        for attempt in range(attempts):
            try:
                return self._client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    f"GeminiKeyManager: attempt {attempt + 1}/{attempts} failed with: {exc}"
                )
                if self._should_rotate(exc) and self._rotate():
                    continue  # retry with the new key
                else:
                    raise  # non-retriable error or only one key — re-raise immediately

        raise last_exc  # all keys exhausted


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_key_manager_instance: Optional[GeminiKeyManager] = None


def get_key_manager() -> GeminiKeyManager:
    global _key_manager_instance
    if _key_manager_instance is None:
        _key_manager_instance = GeminiKeyManager(Config())
    return _key_manager_instance
