"""程序入口: python -m dezhu_agent."""

from dezhu_agent.config import get_config


def main() -> None:
    config = get_config()
    print(f"启动 {config.APP_NAME} (env={config.ENV})")


if __name__ == "__main__":
    main()
