# AGENTS.md — dezhu-agent

## Quick reference

```bash
uv sync                          # install/sync deps (always first)
uv add <pkg>                     # add prod dep
uv add --dev <pkg>               # add dev dep
uv run python -m dezhu_agent     # run the agent
```

## Verification pipeline (run in this order)

```bash
uv run ruff check src/ tests/    # lint
uv run ruff format --check src/ tests/  # format check (omit --check to auto-fix)
uv run mypy src/                 # type check (strict mode)
uv run pytest                    # test + coverage
```

## Key conventions

- **Python 3.12** — pinned in `.python-version`, uv auto-manages the interpreter.
- **`src/` layout** — all source under `src/dezhu_agent/`. Tests import via `from dezhu_agent.xxx`, never relative. `pyproject.toml` sets `pythonpath = ["src"]` so pytest finds it.
- **`uv` is the only package manager** — never use `pip` or `poetry`. Dependencies in `[dependency-groups]` and `[project].dependencies` of `pyproject.toml`. Lockfile `uv.lock` is committed.
- **Dual quotes + line-length 120** — ruff format enforces this. Run `uv run ruff format src/ tests/` before committing.
- **RUF001 ignored** — Chinese fullwidth punctuation is allowed (project uses Chinese).
- **mypy strict** — all functions must have type annotations (`disallow_untyped_defs = true`).

## Configuration

- `src/dezhu_agent/config.py` uses `pydantic-settings` (`BaseSettings`). It auto-loads `.env` (not committed). Copy `.env.example` to `.env` for local overrides.
- `@lru_cache` on `get_config()` ensures a single `Settings` instance.

## Pre-commit

```bash
uv run pre-commit install        # one-time: enable git hooks (ruff + mypy on commit)
```

## Testing

- **No test files yet** — create tests under `tests/`. `conftest.py` has a `settings` fixture.
- Coverage is enabled by default (`--cov=dezhu_agent`). Reports printed to terminal.
- Run a single test: `uv run pytest tests/test_foo.py -k "test_name"`

## Architecture

```
src/dezhu_agent/
  __main__.py      # entrypoint: python -m dezhu_agent
  config.py        # pydantic-settings, reads .env
  core/            # core business logic
  services/        # service layer
  models/          # pydantic / dataclass models
  utils/           # utilities
tests/             # pytest
```
