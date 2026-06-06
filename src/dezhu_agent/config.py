"""配置管理: 环境变量 -> pydantic Settings 自动校验."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # App
    APP_NAME: str = "dezhu-agent"
    ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    # 预留扩展: 后续在这里添加数据库/Redis/LLM 等配置项


@lru_cache
def get_config() -> Settings:
    return Settings()
