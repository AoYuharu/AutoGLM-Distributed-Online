"""
Configuration module for the server
"""
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_SERVER_CONFIG_PATH = REPO_ROOT / "config" / "server-web.yaml"


class ServerYamlConfigSettingsSource(PydanticBaseSettingsSource):
    """Load only the root shared YAML file's server section as flat settings."""

    def __init__(self, settings_cls: type[BaseSettings], yaml_path: Path | None = None):
        super().__init__(settings_cls)
        self.yaml_path = yaml_path or SHARED_SERVER_CONFIG_PATH
        self._cached_data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._cached_data is not None:
            return self._cached_data

        if not self.yaml_path.exists():
            self._cached_data = {}
            return self._cached_data

        with self.yaml_path.open("r", encoding="utf-8") as handle:
            raw_data = yaml.safe_load(handle) or {}

        server = raw_data.get("server")
        if not isinstance(server, dict):
            self._cached_data = {}
            return self._cached_data

        mappings: dict[tuple[str, ...], str] = {
            ("public_base_url",): "PUBLIC_BASE_URL",
            ("websocket_public_url",): "WEBSOCKET_PUBLIC_URL",
            ("host",): "HOST",
            ("port",): "PORT",
            ("debug",): "DEBUG",
            ("database_url",): "DATABASE_URL",
            ("storage_path",): "STORAGE_PATH",
            ("cors_origins",): "CORS_ORIGINS",
            ("websocket", "heartbeat_interval"): "WS_HEARTBEAT_INTERVAL",
            ("websocket", "heartbeat_timeout"): "WS_HEARTBEAT_TIMEOUT",
            ("websocket", "max_connections"): "WS_MAX_CONNECTIONS",
            ("react", "core_threads"): "REACT_CORE_THREADS",
            ("react", "max_threads"): "REACT_MAX_THREADS",
            ("react", "reason_timeout"): "REACT_REASON_TIMEOUT",
            ("react", "ai_timeout"): "REACT_AI_TIMEOUT",
            ("react", "ai_max_retries"): "REACT_AI_MAX_RETRIES",
            ("react", "ack_max_retries"): "REACT_ACK_MAX_RETRIES",
            ("react", "ack_retry_interval"): "REACT_ACK_RETRY_INTERVAL",
            ("react", "ack_timeout"): "REACT_ACK_TIMEOUT",
            ("react", "observe_timeout"): "REACT_OBSERVE_TIMEOUT",
            ("react", "max_observe_error_retries"): "REACT_MAX_OBSERVE_ERROR_RETRIES",
            ("ai", "base_url"): "PHONE_AGENT_BASE_URL",
            ("ai", "api_key"): "PHONE_AGENT_API_KEY",
            ("ai", "model"): "PHONE_AGENT_MODEL",
            ("ai", "timeout"): "PHONE_AGENT_TIMEOUT",
            ("logging", "level"): "LOG_LEVEL",
            ("logging", "file"): "LOG_FILE",
            ("logging", "max_bytes"): "LOG_MAX_BYTES",
            ("logging", "backup_count"): "LOG_BACKUP_COUNT",
            ("jwt", "secret"): "JWT_SECRET",
            ("jwt", "algorithm"): "JWT_ALGORITHM",
            ("jwt", "expire_hours"): "JWT_EXPIRE_HOURS",
        }

        flattened: dict[str, Any] = {}
        for path, target in mappings.items():
            current: Any = server
            for segment in path:
                if not isinstance(current, dict) or segment not in current:
                    current = None
                    break
                current = current[segment]
            if current is not None:
                flattened[target] = current

        self._cached_data = flattened
        return self._cached_data

    def get_field_value(self, field, field_name: str) -> tuple[Any, str, bool]:
        data = self._load()
        return data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return self._load()


class Settings(BaseSettings):
    """Server settings"""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # Server
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    WEBSOCKET_PUBLIC_URL: str = "ws://localhost:8000"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True

    # Database
    DATABASE_URL: str = "sqlite:///./data/autoglm.db"

    # JWT
    JWT_SECRET: str = "your-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24

    # WebSocket
    WS_HEARTBEAT_INTERVAL: int = 30
    WS_HEARTBEAT_TIMEOUT: int = 90
    WS_MAX_CONNECTIONS: int = 10000

    # ReAct runtime
    REACT_CORE_THREADS: int = 4
    REACT_MAX_THREADS: int = 8
    REACT_REASON_TIMEOUT: int = 30
    REACT_AI_TIMEOUT: float = 10.0
    REACT_AI_MAX_RETRIES: int = 3
    REACT_ACK_MAX_RETRIES: int = 3
    REACT_ACK_RETRY_INTERVAL: float = 15.0
    REACT_ACK_TIMEOUT: float = 15.0
    REACT_OBSERVE_TIMEOUT: float = 30.0
    REACT_MAX_OBSERVE_ERROR_RETRIES: int = 2

    # Storage
    STORAGE_PATH: str = "./data/storage"

    # AI Model (Zhipu AI)
    PHONE_AGENT_BASE_URL: str = "https://open.bigmodel.cn/api/paas/v4"
    PHONE_AGENT_API_KEY: str = "EMPTY"
    PHONE_AGENT_MODEL: str = "autoglm-phone"
    PHONE_AGENT_TIMEOUT: int = 120  # 模型调用超时（秒）

    # CORS
    CORS_ORIGINS: str = "*"

    # Logging
    LOG_LEVEL: str = "INFO"  # DEBUG, INFO, WARNING, ERROR
    LOG_FILE: str = "./logs/server.log"
    LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 10MB
    LOG_BACKUP_COUNT: int = 5

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            ServerYamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


settings = get_settings()

# Ensure data directory exists
Path("./data").mkdir(parents=True, exist_ok=True)
Path(settings.STORAGE_PATH).mkdir(parents=True, exist_ok=True)
