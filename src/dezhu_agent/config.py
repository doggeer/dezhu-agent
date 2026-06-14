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
    HERMES_DIR: str = ".hermes/"
    PROMPT_MAX_FILE_CHARS: int = 20000

    # --- Compression parameters ---
    COMPRESSION_THRESHOLD: int = 55000
    PROTECT_FIRST: int = 3
    KEEP_RECENT_TOOL_RESULTS: int = 3
    TAIL_TOKEN_BUDGET: int = 20000
    SUMMARY_MAX_TOKENS: int = 3000
    SUMMARY_PER_MSG_CHARS: int = 2000
    COMPRESSION_MIN_SHRINK: float = 0.9
    COMPRESSION_MODEL: str = "deepseek-v4-flash"
    MODEL_MAX_CONTEXT_TOKENS: int = 65536

    # --- File tool security ---
    ALLOWED_PATHS: str = "."

    # --- Task ToDo ---
    TODO_REMINDER_ROUNDS: int = 3


@lru_cache
def get_config() -> Settings:
    return Settings()
