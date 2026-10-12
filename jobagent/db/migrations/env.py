"""Alembic environment: online mode only, SQLite batch mode, URL from config or caller."""

from pathlib import Path

from alembic import context

from jobagent.config import AppConfig
from jobagent.db.engine import create_db_engine
from jobagent.db.models import Base

config = context.config


def _database_path() -> Path:
    # `python -m jobagent.db init` and the tests pass the path explicitly.
    explicit = config.attributes.get("db_path")
    if explicit is not None:
        return Path(explicit)
    return AppConfig.from_yaml().database.resolved_path


def run_migrations_online() -> None:
    # Batch migrations recreate tables, so foreign keys must be off while they run.
    engine = create_db_engine(_database_path(), foreign_keys=False)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
