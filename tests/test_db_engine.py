from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError, StatementError
from sqlalchemy.orm import Session, sessionmaker

from jobagent.db.engine import session_scope
from jobagent.db.models import Application, Channel, Job
from tests.helpers import make_job


def test_pragmas_applied_on_connect(engine: Engine) -> None:
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert str(conn.exec_driver_sql("PRAGMA journal_mode").scalar()).lower() == "wal"
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000


def test_foreign_keys_are_enforced(session: Session) -> None:
    session.add(Application(job_id=999, channel=Channel.EMAIL))
    with pytest.raises(IntegrityError):
        session.flush()


def test_session_scope_commits_on_success(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as s:
        make_job(s, 1)
    with session_scope(factory) as s:
        assert s.query(Job).count() == 1


def test_session_scope_rolls_back_on_error(factory: sessionmaker[Session]) -> None:
    with pytest.raises(RuntimeError), session_scope(factory) as s:
        make_job(s, 1)
        raise RuntimeError("boom")
    with session_scope(factory) as s:
        assert s.query(Job).count() == 0


def test_timestamps_round_trip_as_utc(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as s:
        job = make_job(s, 1)
        job.claimed_until = datetime(2026, 1, 1, 12, 0, tzinfo=UTC) + timedelta(hours=1)
    with session_scope(factory) as s:
        job = s.query(Job).one()
        assert job.claimed_until == datetime(2026, 1, 1, 13, 0, tzinfo=UTC)
        assert job.claimed_until.tzinfo is not None
        assert job.discovered_at.tzinfo is not None


def test_naive_datetimes_are_rejected(session: Session) -> None:
    job = make_job(session, 1)
    job.claimed_until = datetime(2026, 1, 1, 12, 0)  # noqa: DTZ001 - deliberately naive
    with pytest.raises(StatementError):
        session.flush()


def test_events_are_append_only(session: Session) -> None:
    session.execute(
        text(
            "INSERT INTO events (ts, entity_type, entity_id, action, actor) "
            "VALUES ('2026-01-01 00:00:00', 'job', '1', 'created', 'test')"
        )
    )
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text("UPDATE events SET actor = 'someone'"))
    session.rollback()
    session.execute(
        text(
            "INSERT INTO events (ts, entity_type, entity_id, action, actor) "
            "VALUES ('2026-01-01 00:00:00', 'job', '1', 'created', 'test')"
        )
    )
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text("DELETE FROM events"))
