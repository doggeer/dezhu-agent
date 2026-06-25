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
