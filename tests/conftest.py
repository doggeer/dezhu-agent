"""pytest 共享 fixtures."""

import pytest

from dezhu_agent.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings()
