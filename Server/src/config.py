"""
Configuration module for the server
"""
import os
from pathlib import Path
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Server settings"""

    # Server
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

    # Storage
    STORAGE_PATH: str = "./data/storage"

    # AI Model (Zhipu AI)
    PHONE_AGENT_BASE_URL: str = "https://open.bigmodel.cn/api/paas/v4"
    PHONE_AGENT_API_KEY: str = "EMPTY"
    PHONE_AGENT_MODEL: str = "autoglm-phone"
    PHONE_AGENT_TIMEOUT: int = 120  # 模型调用超时（秒）

    # Logging
    LOG_LEVEL: str = "INFO"  # DEBUG, INFO, WARNING, ERROR
    LOG_FILE: str = "./logs/server.log"
    LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 10MB
    LOG_BACKUP_COUNT: int = 5

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


settings = get_settings()

# Ensure data directory exists
Path("./data").mkdir(exist_ok=True)
Path(settings.STORAGE_PATH).mkdir(exist_ok=True)
