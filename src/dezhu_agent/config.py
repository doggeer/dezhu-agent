"""配置管理：从 .env 文件和环境变量加载运行时配置."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（pyproject.toml 所在目录）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 加载 .env 文件（如果存在）
load_dotenv(PROJECT_ROOT / ".env")


def _getenv_required(key: str) -> str:
    """获取必需的环境变量，缺失时 fail-fast."""
    value = os.environ.get(key)
    if not value:
        print(f"Error: {key} is not set.", file=sys.stderr)
        hint = f"Hint: create a .env file at {PROJECT_ROOT / '.env'} with {key}=sk-..."
        print(hint, file=sys.stderr)
        sys.exit(1)
    return value


# --- 公开配置常量 ---

OPENAI_API_KEY: str | None = os.environ.get("OPENAI_API_KEY")
OPENAI_BASE_URL: str = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com")
MODEL_NAME: str = os.environ.get("MODEL_NAME", "deepseek-flash")
ITERATION_BUDGET: int = int(os.environ.get("ITERATION_BUDGET", "90"))
API_TIMEOUT: int = int(os.environ.get("API_TIMEOUT", "30"))

# DeepSeek 思考模式
THINKING_ENABLED: bool = os.environ.get("THINKING_ENABLED", "false").lower() in ("true", "1", "yes")
REASONING_EFFORT: str = os.environ.get("REASONING_EFFORT", "high")

# 流式输出
STREAM_MODE: bool = os.environ.get("STREAM_MODE", "false").lower() in ("true", "1", "yes")

# 消息持久化
DEZHU_DB_PATH: str = os.environ.get(
    "DEZHU_DB_PATH",
    str(PROJECT_ROOT / ".dezhu-agent" / "dezhu-agent.db"),
)

# 用户项目目录（AGENTS.md 所在目录），默认当前工作目录
PROJECT_DIR: Path = Path(
    os.environ.get("DEZHU_PROJECT_DIR", str(Path.cwd()))
)

# --- 上下文压缩 ---
COMPRESSION_ENABLED: bool = os.environ.get("COMPRESSION_ENABLED", "true").lower() in ("true", "1", "yes")
COMPRESSION_TRIGGER_RATIO: float = float(os.environ.get("COMPRESSION_TRIGGER_RATIO", "0.7"))
COMPRESSION_PREFLIGHT_RATIO: float = float(os.environ.get("COMPRESSION_PREFLIGHT_RATIO", "0.8"))
COMPRESSION_AUX_MODEL: str = os.environ.get("COMPRESSION_AUX_MODEL", "deepseek-v4-flash")
COMPRESSION_AUX_API_KEY: str | None = os.environ.get("COMPRESSION_AUX_API_KEY") or OPENAI_API_KEY
COMPRESSION_AUX_BASE_URL: str = os.environ.get("COMPRESSION_AUX_BASE_URL", OPENAI_BASE_URL)
COMPRESSION_WINDOW_SIZE: int = int(os.environ.get("COMPRESSION_WINDOW_SIZE", "200000"))

# --- 日志 ---
DEZHU_LOG_LEVEL: str = os.environ.get("DEZHU_LOG_LEVEL", "INFO").upper()
DEZHU_LOG_DIR: str = os.environ.get(
    "DEZHU_LOG_DIR",
    str(PROJECT_ROOT / "logs"),
)

# --- 退避参数 ---
BACKOFF_BASE_DELAY: int = int(os.environ.get("DEZHU_BACKOFF_BASE_DELAY", "5"))
BACKOFF_MAX_DELAY: int = int(os.environ.get("DEZHU_BACKOFF_MAX_DELAY", "60"))
RETRY_TIMEOUT: int = int(os.environ.get("DEZHU_RETRY_TIMEOUT", "120"))


# --- 提供商配置 ---
def _parse_provider_config() -> list[dict]:
    """从环境变量解析多提供商配置.

    DEZHU_PROVIDER_CONFIG 格式: provider1:modelA,modelB|provider2:modelC

    每个提供商的 api_key 和 base_url 从 DEZHU_<NAME>_API_KEY / DEZHU_<NAME>_BASE_URL
    读取，fallback 到全局 OPENAI_API_KEY / OPENAI_BASE_URL。
    若 DEZHU_PROVIDER_CONFIG 未设置，返回仅含主模型的默认配置。
    """
    raw = os.environ.get("DEZHU_PROVIDER_CONFIG", "")
    if not raw:
        return [{
            "name": "default",
            "models": [MODEL_NAME],
            "api_key": OPENAI_API_KEY,
            "base_url": OPENAI_BASE_URL,
        }]

    providers: list[dict] = []
    for group in raw.split("|"):
        group = group.strip()
        if not group:
            continue
        if ":" not in group:
            continue
        name, models_str = group.split(":", 1)
        name = name.strip()
        models = [m.strip() for m in models_str.split(",") if m.strip()]
        if not models:
            continue
        provider_upper = name.upper()
        api_key = os.environ.get(f"DEZHU_{provider_upper}_API_KEY") or OPENAI_API_KEY
        base_url = os.environ.get(f"DEZHU_{provider_upper}_BASE_URL") or OPENAI_BASE_URL
        providers.append({
            "name": name,
            "models": models,
            "api_key": api_key,
            "base_url": base_url,
        })

    if not providers:
        return [{
            "name": "default",
            "models": [MODEL_NAME],
            "api_key": OPENAI_API_KEY,
            "base_url": OPENAI_BASE_URL,
        }]
    return providers


PROVIDER_CONFIGS: list[dict] = _parse_provider_config()
