import logging
import threading
import time
from typing import List, Optional
from google import genai
from google.genai.types import GenerateContentConfig
from core.config import Config

logger = logging.getLogger(__name__)

# HTTP status codes that should trigger key rotation
ROTATE_ON_STATUS = {429, 500, 503}
ROTATE_ON_KEYWORDS = ["quota", "rate limit", "resource exhausted", "overloaded", "unavailable"]

# Transient server errors that should be retried with backoff (even with a single key)
RETRY_ON_STATUS = {500, 502, 503}
RETRY_ON_KEYWORDS = ["internal server error", "bad gateway", "unavailable", "snapshot regeneration"]
MAX_RETRIES_SINGLE_KEY = 3
RETRY_BACKOFF_SECONDS = [1.0, 3.0, 5.0]


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
        self._base_url: Optional[str] = self._normalize_base_url(config.gemini_base_url)
        self._current_index: int = 0

        if not self._keys:
            raise ValueError("GeminiKeyManager: no API keys provided.")

        self._client = self._build_client(self._keys[self._current_index])
        logger.info(f"GeminiKeyManager initialised with {len(self._keys)} key(s).")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_base_url(url: Optional[str]) -> Optional[str]:
        """
        Normalize Gemini base URL.
        The google-genai SDK automatically appends '/v1beta' (or '/v1alpha') to the base_url.
        If a user specifies a URL ending in '/v1beta', '/v1alpha', or '/v1', strip it to prevent
        double prefixes like '/v1beta/v1beta/models/...'.
        """
        if not url:
            return None
        url = url.strip().rstrip('/')
        for suffix in ['/v1beta', '/v1alpha', '/v1']:
            if url.endswith(suffix):
                url = url[:-len(suffix)].rstrip('/')
        return url or None

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

    def _is_transient_server_error(self, exc: Exception) -> bool:
        """Return True if this is a transient server error worth retrying with backoff."""
        msg = str(exc).lower()
        if any(kw in msg for kw in RETRY_ON_KEYWORDS):
            return True
        for code in RETRY_ON_STATUS:
            if str(code) in msg:
                return True
        return False

    def _rotate(self) -> bool:
        """
        Advance to the next key. Returns True if a new key was selected,
        False if only one key is available.
        """
        with self._lock:
            if len(self._keys) <= 1:
                return False
            self._current_index = (self._current_index + 1) % len(self._keys)
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
        normalized_url = self._normalize_base_url(new_base_url)
        with self._lock:
            if self._base_url != normalized_url:
                self._base_url = normalized_url
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

        normalized_url = self._normalize_base_url(base_url)

        with self._lock:
            changed = False
            if self._keys != keys:
                logger.info(f"GeminiKeyManager: updating keys pool ({len(self._keys)} -> {len(keys)} keys)")
                self._keys = keys
                self._current_index = min(self._current_index, len(keys) - 1)
                changed = True

            if self._base_url != normalized_url:
                logger.info(f"GeminiKeyManager: updating base URL ({self._base_url} -> {normalized_url})")
                self._base_url = normalized_url
                changed = True

            if changed:
                self._client = self._build_client(self._keys[self._current_index])

    def generate_content(self, model: str, contents, config: GenerateContentConfig):
        """
        Synchronous wrapper around client.models.generate_content with
        automatic key rotation on retriable errors and backoff retry for
        transient server errors (500, 502, 503).
        """
        last_exc = None
        attempts = len(self._keys)  # try each key at most once per call

        for attempt in range(attempts):
            # For each key, retry transient server errors with backoff
            for retry in range(MAX_RETRIES_SINGLE_KEY):
                try:
                    return self._client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=config,
                    )
                except Exception as exc:
                    last_exc = exc

                    # If it's a transient server error and we have retries left, backoff and retry same key
                    if self._is_transient_server_error(exc) and retry < MAX_RETRIES_SINGLE_KEY - 1:
                        backoff = RETRY_BACKOFF_SECONDS[retry] if retry < len(RETRY_BACKOFF_SECONDS) else RETRY_BACKOFF_SECONDS[-1]
                        logger.warning(
                            f"GeminiKeyManager: attempt {attempt + 1}/{attempts}, "
                            f"retry {retry + 1}/{MAX_RETRIES_SINGLE_KEY} failed with transient error: {exc}. "
                            f"Retrying in {backoff}s..."
                        )
                        time.sleep(backoff)
                        continue

                    logger.warning(
                        f"GeminiKeyManager: attempt {attempt + 1}/{attempts} failed with: {exc}"
                    )
                    if self._should_rotate(exc) and self._rotate():
                        break  # break inner retry loop, continue with next key
                    else:
                        raise  # non-retriable error or only one key — re-raise immediately
            else:
                # Inner loop exhausted all retries without success, try next key
                if self._should_rotate(last_exc) and self._rotate():
                    continue
                raise last_exc

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
