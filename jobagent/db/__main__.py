"""Tiny DB CLI: `python -m jobagent.db init|status`."""

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from jobagent.config import AppConfig
from jobagent.db import repo
from jobagent.db.engine import create_db_engine, create_session_factory, session_scope
from jobagent.db.models import Application, Job

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def upgrade(db_path: Path) -> None:
    """Apply all migrations to the database at `db_path`."""
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(MIGRATIONS_DIR))
    alembic_config.attributes["db_path"] = db_path
    command.upgrade(alembic_config, "head")


def _status(db_path: Path) -> None:
    if not db_path.exists():
        print(f"No database at {db_path}. Run: python -m jobagent.db init")
        return
    factory = create_session_factory(create_db_engine(db_path))
    with session_scope(factory) as session:
        print(f"Database: {db_path}")
        for table, count in repo.row_counts(session).items():
            print(f"  {table}: {count}")
        for label, model in (("jobs", Job), ("applications", Application)):
            counts = repo.status_counts(session, model)
            print(f"{label} by status: {counts or '(none)'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobagent.db")
    parser.add_argument("command", choices=["init", "status"])
    args = parser.parse_args(argv)

    db_path = AppConfig.from_yaml().database.resolved_path
    if args.command == "init":
        upgrade(db_path)
        print(f"Database ready at {db_path}")
    else:
        _status(db_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
