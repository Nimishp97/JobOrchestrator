"""Entry point: `uv run python -m jobagent`."""

import logging
import sys

from pydantic import ValidationError

from jobagent.config import AppConfig, Settings
from jobagent.logging_setup import setup_logging

logger = logging.getLogger("jobagent")


def main() -> int:
    try:
        settings = Settings()
        config = AppConfig.from_yaml()
    except (ValidationError, FileNotFoundError) as exc:
        print(f"Invalid configuration:\n{exc}", file=sys.stderr)
        return 1

    setup_logging(secrets=settings.secret_values())
    logger.info("Config loaded | dry_run=%s kill_switch=%s", config.dry_run, config.kill_switch)
    logger.info("Anthropic API key set: %s", settings.api_key_set)
    return 0


if __name__ == "__main__":
    sys.exit(main())
