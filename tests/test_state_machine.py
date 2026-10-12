import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from jobagent.db import repo
from jobagent.db.engine import session_scope
from jobagent.db.models import (
    Application,
    ApplicationStatus,
    EntityType,
    Event,
    Job,
    JobStatus,
)
from jobagent.db.state_machine import (
    APPLICATION_TRANSITIONS,
    JOB_TRANSITIONS,
    ConcurrentModification,
    IllegalTransition,
    transition_application,
    transition_job,
)
from tests.helpers import make_application, make_job

LEGAL_JOB = [(a, b) for a, targets in JOB_TRANSITIONS.items() for b in targets]
ILLEGAL_JOB = [
    (a, b) for a in JobStatus for b in JobStatus if a != b and b not in JOB_TRANSITIONS[a]
]
LEGAL_APP = [(a, b) for a, targets in APPLICATION_TRANSITIONS.items() for b in targets]
ILLEGAL_APP = [
    (a, b)
    for a in ApplicationStatus
    for b in ApplicationStatus
    if a != b and b not in APPLICATION_TRANSITIONS[a]
]


def event_count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(Event)).scalar_one()


def test_every_status_has_rules() -> None:
    assert set(JOB_TRANSITIONS) == set(JobStatus)
    assert set(APPLICATION_TRANSITIONS) == set(ApplicationStatus)


@pytest.mark.parametrize(("src", "dst"), LEGAL_JOB)
def test_legal_job_transition(session: Session, src: JobStatus, dst: JobStatus) -> None:
    job = make_job(session, status=src)
    assert transition_job(session, job, dst, actor="test", payload={"why": "x"}) is True
    assert job.status == dst
    event = session.execute(select(Event)).scalar_one()
    assert (event.entity_type, event.entity_id) == (EntityType.JOB, str(job.id))
    assert (event.from_status, event.to_status) == (src, dst)
    assert event.actor == "test"
    assert event.payload == {"why": "x"}


@pytest.mark.parametrize(("src", "dst"), ILLEGAL_JOB)
def test_illegal_job_transition(session: Session, src: JobStatus, dst: JobStatus) -> None:
    job = make_job(session, status=src)
    with pytest.raises(IllegalTransition):
        transition_job(session, job, dst, actor="test")
    assert job.status == src
    assert event_count(session) == 0


@pytest.mark.parametrize(("src", "dst"), LEGAL_APP)
def test_legal_application_transition(
    session: Session, src: ApplicationStatus, dst: ApplicationStatus
) -> None:
    application = make_application(session, make_job(session), status=src)
    assert transition_application(session, application, dst, actor="test") is True
    assert application.status == dst
    event = session.execute(select(Event)).scalar_one()
    assert (event.entity_type, event.from_status, event.to_status) == (
        EntityType.APPLICATION,
        src,
        dst,
    )


@pytest.mark.parametrize(("src", "dst"), ILLEGAL_APP)
def test_illegal_application_transition(
    session: Session, src: ApplicationStatus, dst: ApplicationStatus
) -> None:
    application = make_application(session, make_job(session), status=src)
    with pytest.raises(IllegalTransition):
        transition_application(session, application, dst, actor="test")
    assert application.status == src
    assert event_count(session) == 0


def test_same_state_is_a_noop(session: Session) -> None:
    job = make_job(session, status=JobStatus.SCORED)
    assert transition_job(session, job, JobStatus.SCORED, actor="test") is False
    assert event_count(session) == 0


def test_entering_sent_stamps_sent_at(session: Session) -> None:
    application = make_application(session, make_job(session), status=ApplicationStatus.SENDING)
    assert application.sent_at is None
    transition_application(session, application, ApplicationStatus.SENT, actor="test")
    assert application.sent_at is not None


def test_job_transition_clears_lease(session: Session) -> None:
    make_job(session, status=JobStatus.DISCOVERED)
    [job] = repo.claim_jobs(
        session, status=JobStatus.DISCOVERED, limit=1, worker="w1", lease_seconds=60
    )
    assert job.claimed_by == "w1"
    transition_job(session, job, JobStatus.SCORED, actor="w1")
    assert job.claimed_by is None
    assert job.claimed_until is None


def test_status_and_event_roll_back_together_when_event_fails(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_scope(factory) as s:
        job_id = make_job(s).id

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("event insert failed")

    monkeypatch.setattr(repo, "record_event", boom)
    with pytest.raises(RuntimeError), session_scope(factory) as s:
        transition_job(s, s.get_one(Job, job_id), JobStatus.SCORED, actor="test")

    with session_scope(factory) as s:
        assert s.get_one(Job, job_id).status == JobStatus.DISCOVERED
        assert event_count(s) == 0


def test_status_and_event_roll_back_together_when_caller_fails(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as s:
        job_id = make_job(s).id

    with pytest.raises(RuntimeError), session_scope(factory) as s:
        transition_job(s, s.get_one(Job, job_id), JobStatus.SCORED, actor="test")
        assert event_count(s) == 1
        raise RuntimeError("later step failed")

    with session_scope(factory) as s:
        assert s.get_one(Job, job_id).status == JobStatus.DISCOVERED
        assert event_count(s) == 0


def test_stale_entity_cannot_overwrite_newer_state(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as s:
        job_id = make_job(s).id

    with session_scope(factory) as s:
        stale = s.get_one(Job, job_id)
        # Someone else advances the row behind our back (bypassing the session's view).
        s.execute(Job.__table__.update().values(status=JobStatus.FAILED.value))
        with pytest.raises(ConcurrentModification):
            transition_job(s, stale, JobStatus.SCORED, actor="test")
        assert event_count(s) == 0


def test_application_is_unique_per_job_and_channel(session: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    from jobagent.db.models import Channel

    job = make_job(session)
    make_application(session, job, Channel.EMAIL)
    make_application(session, job, Channel.PORTAL)  # other channel is fine
    with pytest.raises(IntegrityError):
        make_application(session, job, Channel.EMAIL)
    session.rollback()
    assert session.query(Application).count() == 0
