from pydantic_settings import BaseSettings, SettingsConfigDict

class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    gemini_api_key: str
    gemini_api_model: str
    gemini_gatekeeper_model: str = "gemini-2.5-flash-8b"
    
    elevenlabs_api_key: str = ""
    
    mcp_servers_config: str = "{}"

    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db_name: str = "gemini_bot"