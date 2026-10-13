from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from jobagent.db.__main__ import upgrade
from jobagent.db.engine import create_db_engine, create_session_factory


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    upgrade(path)
    return path


@pytest.fixture
def engine(db_path: Path) -> Iterator[Engine]:
    engine = create_db_engine(db_path)
    yield engine
    engine.dispose()


@pytest.fixture
def factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def session(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as session:
        yield session
        session.rollback()
