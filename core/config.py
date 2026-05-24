from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    gemini_api_key: str
    # Comma-separated list of additional Gemini API keys for rotation.
    # Example: GEMINI_API_KEYS=key1,key2,key3
    # gemini_api_key is always included automatically as the primary key.
    gemini_api_keys: str = ""
    gemini_api_model: str
    gemini_gatekeeper_model: str = "gemini-3.1-flash-lite"
    gemini_base_url: str | None = None

    elevenlabs_api_key: str = ""
    groq_api_key: str = ""

    mcp_servers_config: str = "{}"

    admin_telegram_id: int = 0
    admin_port: int = 8081

    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db_name: str = "gemini_bot"

    def get_all_api_keys(self) -> List[str]:
        """Return a de-duplicated ordered list of all Gemini API keys (primary first)."""
        keys = [self.gemini_api_key]
        if self.gemini_api_keys:
            for k in self.gemini_api_keys.split(","):
                k = k.strip()
                if k and k not in keys:
                    keys.append(k)
        return keys