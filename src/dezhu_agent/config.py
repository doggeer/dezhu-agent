"""应用配置 —— 基于 pydantic-settings, 从 .env / 环境变量自动加载."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "dezhu-agent"
    ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    BASE_URL: str = "https://api.deepseek.com"
    API_KEY: str = "sk-your-api-key"
    MODEL: str = "deepseek-v4-pro"
    MAX_ITERATIONS: int = 10
    DB_PATH: str = "state.db"


@lru_cache
def get_config() -> Settings:
    return Settings()
