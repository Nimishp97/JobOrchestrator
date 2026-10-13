"""Tiny DB CLI: `python -m jobagent.db init|status`."""

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.engine import make_url

from jobagent.config import AppConfig, Settings
from jobagent.db import repo
from jobagent.db.engine import (
    create_db_engine,
    create_postgres_engine,
    create_session_factory,
    session_scope,
)
from jobagent.db.models import Application, Job

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def upgrade(db_path: Path | None = None, *, url: str | None = None) -> None:
    """Apply all migrations to the Postgres `url`, or else to the SQLite file at `db_path`."""
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(MIGRATIONS_DIR))
    alembic_config.attributes["db_path"] = db_path
    alembic_config.attributes["db_url"] = url
    command.upgrade(alembic_config, "head")


def resolve_target() -> tuple[str | None, Path | None, str]:
    """(postgres url, sqlite path, human label). The label never contains credentials."""
    secret = Settings().DATABASE_URL
    if secret is not None and secret.get_secret_value():
        url = secret.get_secret_value()
        parsed = make_url(url)
        return url, None, f"PostgreSQL {parsed.host}/{parsed.database}"
    path = AppConfig.from_yaml().database.resolved_path
    return None, path, f"SQLite {path}"


def open_engine() -> tuple[Engine, str]:
    """Engine for the configured database (Postgres if DATABASE_URL is set), plus its label."""
    url, path, label = resolve_target()
    if url is not None:
        return create_postgres_engine(url), label
    assert path is not None
    return create_db_engine(path), label


def _status(engine: Engine, label: str) -> None:
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        print(f"Database: {label}")
        for table, count in repo.row_counts(session).items():
            print(f"  {table}: {count}")
        for name, model in (("jobs", Job), ("applications", Application)):
            counts = repo.status_counts(session, model)
            print(f"{name} by status: {counts or '(none)'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobagent.db")
    parser.add_argument("command", choices=["init", "status"])
    args = parser.parse_args(argv)

    url, path, label = resolve_target()
    if args.command == "init":
        upgrade(path, url=url)
        print(f"Database ready: {label}")
        return 0
    if url is not None:
        engine = create_postgres_engine(url)
    elif path is not None and path.exists():
        engine = create_db_engine(path)
    else:
        print(f"No database at {path}. Run: python -m jobagent.db init")
        return 0
    _status(engine, label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
