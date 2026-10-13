from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect

from jobagent.db.__main__ import upgrade
from jobagent.db.models import Base


def test_upgrade_creates_all_tables_on_empty_db(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    assert set(Base.metadata.tables) <= tables
    assert "alembic_version" in tables


def test_upgrade_is_idempotent(db_path: Path) -> None:
    upgrade(db_path)  # already at head: must be a clean no-op


def test_models_and_migration_are_in_sync(engine: Engine) -> None:
    with engine.connect() as conn:
        context = MigrationContext.configure(
            conn, opts={"compare_type": True, "render_as_batch": True}
        )
        assert compare_metadata(context, Base.metadata) == []
