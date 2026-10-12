"""Logging: one consistent format to console and data/agent.log, with secret redaction."""

import logging
import sys
from collections.abc import Iterable
from pathlib import Path

from jobagent.config import PROJECT_ROOT

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DEFAULT_LOG_FILE = PROJECT_ROOT / "data" / "agent.log"
REDACTED = "***REDACTED***"


class RedactingFormatter(logging.Formatter):
    """Replaces known secret values in the final output, including tracebacks."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__(LOG_FORMAT)
        # Longest first so a secret that contains another is fully masked.
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text


def setup_logging(
    secrets: Iterable[str] = (),
    log_file: Path = DEFAULT_LOG_FILE,
    level: int = logging.INFO,
) -> None:
    """Configure the root logger. Safe to call more than once."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    formatter = RedactingFormatter(secrets)

    console = logging.StreamHandler(sys.stderr)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    for handler in (console, file_handler):
        handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
        existing.close()
    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(file_handler)
