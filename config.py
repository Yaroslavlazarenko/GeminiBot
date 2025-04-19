import json
from pathlib import Path
from typing import Dict, Any
from pydantic_settings import BaseSettings, SettingsConfigDict

class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow"
    )

    # Load settings from appsettings.json if available
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Dict[str, Any],
        env_settings: Dict[str, Any],
        dotenv_settings: Dict[str, Any],
    ) -> Dict[str, Any]:
        config_file = Path("appsettings.json")
        if config_file.exists():
            with open(config_file, "r") as f:
                config_data = json.load(f)
                # Flatten nested structure
                if "database" in config_data:
                    init_settings.update({
                        "db_host": config_data["database"].get("host", "localhost"),
                        "db_user": config_data["database"].get("user", "postgres"),
                        "db_password": config_data["database"].get("password", ""),
                        "db_name": config_data["database"].get("name", "gemini_bot"),
                    })
                if "bot" in config_data:
                    init_settings["bot_token"] = config_data["bot"]["token"]
                if "gemini" in config_data:
                    init_settings["gemini_api_key"] = config_data["gemini"].get("api_key", "")
                    init_settings["gemini_model"] = config_data["gemini"].get("model", "gemini-pro")

        # Environment variables take precedence over config file
        init_settings.update(env_settings)
        init_settings.update(dotenv_settings)
        return init_settings

    bot_token: str
    gemini_api_key: str
    gemini_model: str = "gemini-pro"

    db_user: str = "postgres"
    db_password: str
    db_name: str = "gemini_bot"
    db_host: str = "localhost"