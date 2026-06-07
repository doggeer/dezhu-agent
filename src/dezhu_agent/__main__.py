"""程序入口: python -m dezhu_agent."""

from dezhu_agent.config import get_config
from dezhu_agent.core.agent import agent_loop
from dezhu_agent.services.tool_registry import get_tool_registry


def main() -> None:
    config = get_config()
    print(f"启动 {config.APP_NAME} env={config.ENV}")

    get_tool_registry().scan("dezhu_agent.core.tools")

    agent_loop()


if __name__ == "__main__":
    main()
