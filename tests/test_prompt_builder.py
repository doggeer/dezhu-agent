"""Prompt Builder 测试."""

from __future__ import annotations

from pathlib import Path

import pytest

from dezhu_agent.config import get_config
from dezhu_agent.core.prompt_builder import (
    DEFAULT_SOUL,
    _build_identity,
    _build_memory,
    _build_skills,
    _build_timestamp_info,
    _find_git_root,
    _find_project_config_file,
    _get_hermes_dir,
    _read_and_truncate,
    build_system_prompt,
)


# ---- 辅助函数测试 ----
class TestReadAndTruncate:
    def test_under_limit(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("hello")
        assert _read_and_truncate(f) == "hello"

    def test_exact_limit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(get_config(), "PROMPT_MAX_FILE_CHARS", 5)
        f = tmp_path / "test.md"
        f.write_text("hello")
        assert _read_and_truncate(f) == "hello"

    def test_over_limit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(get_config(), "PROMPT_MAX_FILE_CHARS", 10)
        f = tmp_path / "test.md"
        f.write_text("hello world, this is a long message")
        result = _read_and_truncate(f)
        assert result.startswith("hello worl")
        assert "truncated" in result
        assert len(result.split("\n")[0]) == 10


class TestFindGitRoot:
    def test_finds_git_in_cwd(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        assert _find_git_root(tmp_path) == tmp_path

    def test_finds_git_in_parent(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        child = tmp_path / "a" / "b"
        child.mkdir(parents=True)
        assert _find_git_root(child) == tmp_path

    def test_no_git_fallback(self, tmp_path: Path) -> None:
        assert _find_git_root(tmp_path) == tmp_path


class TestFindProjectConfigFile:
    def test_finds_hermes_md_in_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
        (tmp_path / "HERMES.md").write_text("hermes content")
        assert _find_project_config_file() == "hermes content"

    def test_finds_dot_hermes_md_in_parent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".git").mkdir()
        parent = tmp_path / "parent"
        parent.mkdir()
        (parent / ".hermes.md").write_text("parent hermes")
        child = parent / "child"
        child.mkdir()
        monkeypatch.setattr(Path, "cwd", lambda: child)
        assert _find_project_config_file() == "parent hermes"

    def test_hermes_priority_over_agents(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
        (tmp_path / "HERMES.md").write_text("hermes")
        (tmp_path / "AGENTS.md").write_text("agents")
        assert _find_project_config_file() == "hermes"

    def test_falls_back_to_agents(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
        (tmp_path / "AGENTS.md").write_text("agents")
        assert _find_project_config_file() == "agents"

    def test_falls_back_to_claude(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
        (tmp_path / "CLAUDE.md").write_text("claude")
        assert _find_project_config_file() == "claude"

    def test_falls_back_to_cursorrules(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
        (tmp_path / ".cursorrules").write_text("cursor rules")
        assert _find_project_config_file() == "cursor rules"

    def test_none_when_no_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
        assert _find_project_config_file() is None

    def test_case_insensitive_agents(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
        (tmp_path / "agents.md").write_text("lowercase agents")
        assert _find_project_config_file() == "lowercase agents"

    def test_case_insensitive_claude(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
        (tmp_path / "claude.md").write_text("lowercase claude")
        assert _find_project_config_file() == "lowercase claude"


# ---- Layer 测试 ----
class TestBuildIdentity:
    def test_default_when_no_soul(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "dezhu_agent.core.prompt_builder._get_hermes_dir",
            lambda: tmp_path,
        )
        assert _build_identity() == DEFAULT_SOUL

    def test_loads_soul_md(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "dezhu_agent.core.prompt_builder._get_hermes_dir",
            lambda: tmp_path,
        )
        (tmp_path / "SOUL.md").write_text("custom soul content")
        assert _build_identity() == "custom soul content"


class TestBuildMemory:
    def test_empty_when_no_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "dezhu_agent.core.prompt_builder._get_hermes_dir",
            lambda: tmp_path,
        )
        assert _build_memory() == ""

    def test_loads_memory_md(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "dezhu_agent.core.prompt_builder._get_hermes_dir",
            lambda: tmp_path,
        )
        (tmp_path / "MEMORY.md").write_text("user remembers: Python is fun")
        result = _build_memory()
        assert "# Memory" in result
        assert "user remembers: Python is fun" in result

    def test_loads_user_md(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "dezhu_agent.core.prompt_builder._get_hermes_dir",
            lambda: tmp_path,
        )
        (tmp_path / "USER.md").write_text("prefers short answers")
        result = _build_memory()
        assert "# User Preferences" in result
        assert "prefers short answers" in result

    def test_loads_both(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "dezhu_agent.core.prompt_builder._get_hermes_dir",
            lambda: tmp_path,
        )
        (tmp_path / "MEMORY.md").write_text("memory content")
        (tmp_path / "USER.md").write_text("user content")
        result = _build_memory()
        assert "# Memory" in result
        assert "memory content" in result
        assert "# User Preferences" in result
        assert "user content" in result


class TestBuildSkills:
    def test_empty_when_no_skills_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "dezhu_agent.core.prompt_builder._get_hermes_dir",
            lambda: tmp_path,
        )
        assert _build_skills() == ""

    def test_empty_when_skills_dir_exists_but_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "dezhu_agent.core.prompt_builder._get_hermes_dir",
            lambda: tmp_path,
        )
        (tmp_path / "skills").mkdir()
        assert _build_skills() == ""


class TestBuildTimestampInfo:
    def test_contains_model(self) -> None:
        result = _build_timestamp_info("gpt-4")
        assert "Model: gpt-4" in result

    def test_contains_conversation_started(self) -> None:
        result = _build_timestamp_info("test-model")
        assert "Conversation started:" in result
        assert "UTC" in result


# ---- 集成测试 ----
class TestBuildSystemPrompt:
    def test_full_assembly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "dezhu_agent.core.prompt_builder._get_hermes_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
        (tmp_path / "SOUL.md").write_text("You are a robot")
        (tmp_path / "MEMORY.md").write_text("user likes Python")
        (tmp_path / "AGENTS.md").write_text("this is a Go project")

        result = build_system_prompt(model="test-model")
        assert "You are a robot" in result
        assert "# Memory" in result
        assert "user likes Python" in result
        assert "# Project Context" in result
        assert "this is a Go project" in result
        assert "Conversation started:" in result
        assert "Model: test-model" in result

    def test_minimal_assembly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "dezhu_agent.core.prompt_builder._get_hermes_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
        result = build_system_prompt(model="minimal-model")
        assert DEFAULT_SOUL in result
        assert "Conversation started:" in result
        assert "Model: minimal-model" in result
        assert "# Memory" not in result
        assert "# Available Skills" not in result
        assert "# Project Context" not in result

    def test_layers_separated_by_double_newline(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "dezhu_agent.core.prompt_builder._get_hermes_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
        (tmp_path / "SOUL.md").write_text("identity")
        (tmp_path / "MEMORY.md").write_text("memory")

        result = build_system_prompt(model="m")
        parts = result.split("\n\n")
        assert len(parts) >= 3  # identity, memory, timestamp

    def test_no_skills_layer_when_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "dezhu_agent.core.prompt_builder._get_hermes_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
        result = build_system_prompt(model="m")
        assert "# Available Skills" not in result

    def test_hermes_md_supersedes_agents_md(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "dezhu_agent.core.prompt_builder._get_hermes_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
        (tmp_path / "HERMES.md").write_text("HERMES rules")
        (tmp_path / "AGENTS.md").write_text("AGENTS rules")
        result = build_system_prompt(model="m")
        assert "HERMES rules" in result
        assert "AGENTS rules" not in result


# ---- _get_hermes_dir 测试 ----
class TestGetHermesDir:
    def test_returns_configured_dir(self) -> None:
        result = _get_hermes_dir()
        assert isinstance(result, Path)
        assert result.name == ".hermes"
