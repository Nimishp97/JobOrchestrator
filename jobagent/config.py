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

    @property
    def api_key_set(self) -> bool:
        return self.ANTHROPIC_API_KEY is not None and bool(
            self.ANTHROPIC_API_KEY.get_secret_value()
        )

    def secret_values(self) -> list[str]:
        """Raw secret strings, for log redaction only."""
        if self.ANTHROPIC_API_KEY is None:
            return []
        value = self.ANTHROPIC_API_KEY.get_secret_value()
        return [value] if value else []


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


class LLMConfig(_Strict):
    # Deliberately no default: model names get deprecated, so it must be set in config.yaml.
    model: str = Field(min_length=1)


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
