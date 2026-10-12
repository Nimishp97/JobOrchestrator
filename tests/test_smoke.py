import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from jobagent.config import DEFAULT_CONFIG_PATH, AppConfig, Settings
from jobagent.logging_setup import REDACTED, setup_logging


def test_safe_defaults() -> None:
    config = AppConfig(llm={"model": "test-model"})
    assert config.dry_run is True
    assert config.kill_switch is False
    assert config.score_threshold == 75


def test_committed_config_is_valid_and_safe() -> None:
    config = AppConfig.from_yaml(DEFAULT_CONFIG_PATH)
    assert config.dry_run is True
    assert config.kill_switch is False


@pytest.mark.parametrize(
    "bad",
    [
        {"score_threshold": 101},
        {"caps": {"email_per_day": -1}},
        {"dry_run": "nope"},
        {"quiet_hours": {"timezone": "Mars/Olympus"}},
        {"dry_runn": False},
        {"llm": {"model": ""}},
    ],
)
def test_invalid_config_raises(bad: dict) -> None:
    data = {"llm": {"model": "test-model"}} | bad
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_missing_model_raises() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate({})


def test_secrets_are_not_logged(tmp_path: Path) -> None:
    secret = "sk-test-very-secret-value"
    settings = Settings(ANTHROPIC_API_KEY=secret, _env_file=None)
    log_file = tmp_path / "agent.log"
    setup_logging(secrets=settings.secret_values(), log_file=log_file)

    logging.getLogger("jobagent").info("leak attempt: %s", secret)
    logging.shutdown()

    content = log_file.read_text(encoding="utf-8")
    assert secret not in content
    assert REDACTED in content
    assert settings.api_key_set is True
