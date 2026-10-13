"""Configuration: secrets from .env (Settings), behavior from config.yaml (AppConfig)."""

from datetime import time
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class Settings(BaseSettings):
    """Secrets and environment-specific paths. Loaded from the environment / .env only."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ANTHROPIC_API_KEY: SecretStr | None = None
    GOOGLE_CREDENTIALS_PATH: Path | None = None
    # Postgres (e.g. Neon). Contains a password, so it lives here and never in config.yaml.
    # When unset, the SQLite file at config.yaml `database.path` is used.
    DATABASE_URL: SecretStr | None = None

    @property
    def api_key_set(self) -> bool:
        return self.ANTHROPIC_API_KEY is not None and bool(
            self.ANTHROPIC_API_KEY.get_secret_value()
        )

    def secret_values(self) -> list[str]:
        """Raw secret strings, for log redaction only."""
        secrets = (self.ANTHROPIC_API_KEY, self.DATABASE_URL)
        return [s.get_secret_value() for s in secrets if s and s.get_secret_value()]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Caps(_Strict):
    """Per-day send limits per channel. 0 disables the channel."""

    email_per_day: int = Field(default=10, ge=0)
    portal_per_day: int = Field(default=10, ge=0)
    linkedin_dm_per_day: int = Field(default=5, ge=0)


class QuietHours(_Strict):
    """No dispatching between start and end (may wrap midnight), in the given timezone."""

    start: time = time(22, 0)
    end: time = time(7, 0)
    timezone: str = "UTC"

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError, OSError) as exc:
            raise ValueError(f"unknown IANA timezone: {value!r}") from exc
        return value


class ModelPrice(_Strict):
    """USD per million tokens. Cache prices are optional; missing ones make the cost unknown."""

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_read: float | None = Field(default=None, ge=0)
    cache_write: float | None = Field(default=None, ge=0)


class TaskOverride(_Strict):
    """Per-task LLM settings; anything left out falls back to the `llm` defaults."""

    model: str | None = Field(default=None, min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, gt=0)
    timeout_seconds: float | None = Field(default=None, gt=0)


class TaskSettings(_Strict):
    """Fully resolved settings for one task."""

    model: str
    temperature: float | None
    max_tokens: int
    timeout_seconds: float


class LLMConfig(_Strict):
    # Deliberately no default: model names get deprecated, so it must be set in config.yaml.
    model: str = Field(min_length=1)
    temperature: float | None = Field(default=0.0, ge=0, le=1)  # None = leave to the API default
    max_tokens: int = Field(default=2048, gt=0)
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_validation_retries: int = Field(default=2, ge=0)
    max_transport_attempts: int = Field(default=4, ge=1)
    backoff_base_seconds: float = Field(default=1.0, ge=0)
    backoff_max_seconds: float = Field(default=30.0, ge=0)
    log_payloads: StrictBool = False  # True writes raw prompts/responses to data/llm_debug/
    daily_budget_usd: float = Field(default=5.0, ge=0)  # UTC day, from llm_calls
    max_calls_per_day: int = Field(default=200, ge=0)
    tasks: dict[str, TaskOverride] = {}
    pricing: dict[str, ModelPrice] = {}  # keyed by model name

    def for_task(self, task: str) -> TaskSettings:
        override = self.tasks.get(task, TaskOverride())
        return TaskSettings(
            model=override.model or self.model,
            temperature=self.temperature if override.temperature is None else override.temperature,
            max_tokens=override.max_tokens or self.max_tokens,
            timeout_seconds=override.timeout_seconds or self.timeout_seconds,
        )


class DatabaseConfig(_Strict):
    path: Path = Path("data/jobagent.db")

    @property
    def resolved_path(self) -> Path:
        """Absolute path; relative values are resolved against the project root."""
        return self.path if self.path.is_absolute() else PROJECT_ROOT / self.path


class AppConfig(_Strict):
    dry_run: StrictBool = True
    kill_switch: StrictBool = False
    score_threshold: int = Field(default=75, ge=0, le=100)
    caps: Caps = Caps()
    quiet_hours: QuietHours = QuietHours()
    database: DatabaseConfig = DatabaseConfig()
    llm: LLMConfig

    @classmethod
    def from_yaml(cls, path: Path = DEFAULT_CONFIG_PATH) -> Self:
        """Load and validate config.yaml. Raises FileNotFoundError or ValidationError."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)
