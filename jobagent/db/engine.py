"""Engine, session factory and session scope for the SQLite database."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from sqlite3 import Connection as SQLiteConnection
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

BUSY_TIMEOUT_MS = 5000


def create_db_engine(path: Path, *, foreign_keys: bool = True) -> Engine:
    """Create an engine for the SQLite file at `path`.

    Every connection gets WAL, a busy timeout and (by default) foreign keys. Every transaction
    starts with BEGIN IMMEDIATE so writers serialize cleanly instead of failing with
    SQLITE_BUSY_SNAPSHOT when a read upgrades to a write; WAL readers are unaffected.
    `foreign_keys=False` is for Alembic batch migrations, which recreate tables.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path.as_posix()}")

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: SQLiteConnection, _record: Any) -> None:
        # Take over transaction control from pysqlite so BEGIN IMMEDIATE below is honoured.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(connection: Connection) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commit on success, roll back on any error."""
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
