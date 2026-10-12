# Decisions

## 001. Use uv for environment and dependency management
- **Decision**: uv with `pyproject.toml` and a committed `uv.lock`; Python pinned to 3.12.
- **Why**: one fast tool for Python install, venv, lockfile, and running. Reproducible installs
  matter for an agent that takes real-world actions.
- **Trade-off**: contributors need uv installed; it is a newer tool than pip/poetry.

## 002. Split secrets (.env) from behavior (config.yaml)
- **Decision**: secrets via `.env` (pydantic-settings); behavior via committed `config.yaml`
  validated by pydantic models with `extra="forbid"`.
- **Why**: behavior is reviewable and versioned, secrets are never committed. A typo in a key
  fails startup instead of silently using a default.
- **Trade-off**: two config sources to understand.

## 003. dry_run defaults to true
- **Decision**: `dry_run=True` in the model and in `config.yaml`; sending requires an explicit
  `false`.
- **Why**: the agent sends email and applications on the user's behalf. A wrong default or a
  missing config must fail safe, not send.
- **Trade-off**: one extra step to go live.

## 004. The LLM model name is configurable, with no default in code
- **Decision**: `llm.model` is a required string in `config.yaml`; no model ID appears in code.
- **Why**: models get deprecated; swapping should be a config change, and a missing value should
  fail fast rather than silently use a stale model.
- **Trade-off**: `config.yaml` is mandatory.

## 005. Redact secrets in the log formatter
- **Decision**: a formatter replaces known secret values in the final log text (including
  tracebacks). Secrets are held as `SecretStr`.
- **Why**: `SecretStr` does not protect against code that unwraps the value into a message.
- **Limit**: only redacts values known at startup; do not log secrets deliberately.
