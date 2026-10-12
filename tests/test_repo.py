import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from jobagent.db import repo
from jobagent.db.engine import session_scope
from jobagent.db.models import ApplicationStatus, Channel, EntityType, Event, Job, JobStatus
from tests.helpers import make_application, make_job

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def upsert(session: Session, **overrides: str | None) -> tuple[Job, bool]:
    fields: dict[str, str | None] = {
        "source": "linkedin",
        "source_job_id": "123",
        "url": "https://linkedin.com/jobs/123?utm_source=x",
        "company": "Acme Inc.",
        "title": "Backend Engineer",
        "location": "Remote",
        "jd_text": "Build things",
    }
    fields.update(overrides)
    return repo.upsert_job_if_new(session, **fields)  # type: ignore[arg-type]


# --- keys ---------------------------------------------------------------------------------


def test_dedupe_key_ignores_case_punctuation_and_legal_suffix() -> None:
    a = repo.make_dedupe_key("Acme Inc.", "Backend  Engineer", "Remote")
    b = repo.make_dedupe_key("ACME", "backend engineer!", "remote")
    assert a == b


def test_dedupe_key_distinguishes_different_jobs() -> None:
    base = repo.make_dedupe_key("Acme", "Backend Engineer", "Remote")
    assert base != repo.make_dedupe_key("Acme", "Senior Backend Engineer", "Remote")
    assert base != repo.make_dedupe_key("Acme", "Backend Engineer", "Berlin")


def test_canonicalize_url_drops_tracking_and_fragment() -> None:
    url = "HTTPS://Example.com/jobs/1/?utm_source=a&id=7&fbclid=z#apply"
    assert repo.canonicalize_url(url) == "https://example.com/jobs/1?id=7"


# --- upsert -------------------------------------------------------------------------------


def test_upsert_creates_then_dedupes(session: Session) -> None:
    job, created = upsert(session)
    assert created is True
    assert job.status == JobStatus.DISCOVERED
    assert job.canonical_url == "https://linkedin.com/jobs/123"

    again, created_again = upsert(session)
    assert created_again is False
    assert again.id == job.id
    assert session.query(Job).count() == 1
    created_events = session.execute(
        select(func.count()).select_from(Event).where(Event.action == "created")
    ).scalar_one()
    assert created_events == 1


def test_upsert_dedupes_across_sources_by_content(session: Session) -> None:
    job, _ = upsert(session)
    other, created = upsert(
        session, source="indeed", source_job_id="zzz", company="acme", title="backend engineer"
    )
    assert created is False
    assert other.id == job.id


def test_upsert_dedupes_same_source_id_even_if_title_edited(session: Session) -> None:
    job, _ = upsert(session)
    other, created = upsert(session, title="Backend Engineer II")
    assert created is False
    assert other.id == job.id


# --- claiming -----------------------------------------------------------------------------


def test_sequential_claims_never_return_the_same_row(session: Session) -> None:
    for n in range(5):
        make_job(session, n)
    first = repo.claim_jobs(
        session, status=JobStatus.DISCOVERED, limit=3, worker="w1", lease_seconds=60, now=NOW
    )
    second = repo.claim_jobs(
        session, status=JobStatus.DISCOVERED, limit=3, worker="w2", lease_seconds=60, now=NOW
    )
    assert len(first) == 3
    assert len(second) == 2
    assert {j.id for j in first}.isdisjoint({j.id for j in second})
    assert {j.claimed_by for j in first} == {"w1"}
    assert {j.claimed_by for j in second} == {"w2"}


def test_only_matching_status_is_claimed(session: Session) -> None:
    make_job(session, 1, JobStatus.DISCOVERED)
    make_job(session, 2, JobStatus.SCORED)
    claimed = repo.claim_jobs(
        session, status=JobStatus.SCORED, limit=10, worker="w1", lease_seconds=60, now=NOW
    )
    assert [j.source_job_id for j in claimed] == ["id-2"]


def test_live_lease_is_not_reclaimable_but_expired_lease_is(session: Session) -> None:
    make_job(session, 1)
    [job] = repo.claim_jobs(
        session, status=JobStatus.DISCOVERED, limit=1, worker="w1", lease_seconds=60, now=NOW
    )
    live = repo.claim_jobs(
        session,
        status=JobStatus.DISCOVERED,
        limit=1,
        worker="w2",
        lease_seconds=60,
        now=NOW + timedelta(seconds=30),
    )
    assert live == []

    expired = repo.claim_jobs(
        session,
        status=JobStatus.DISCOVERED,
        limit=1,
        worker="w2",
        lease_seconds=60,
        now=NOW + timedelta(seconds=61),
    )
    assert [j.id for j in expired] == [job.id]
    assert expired[0].claimed_by == "w2"


def test_release_claim_only_by_holder(session: Session) -> None:
    make_job(session, 1)
    [job] = repo.claim_jobs(
        session, status=JobStatus.DISCOVERED, limit=1, worker="w1", lease_seconds=60, now=NOW
    )
    assert repo.release_claim(session, job, "someone-else") is False
    assert job.claimed_by == "w1"
    assert repo.release_claim(session, job, "w1") is True
    assert job.claimed_by is None
    assert job.claimed_until is None


def test_concurrent_workers_never_share_a_job(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as s:
        for n in range(20):
            make_job(s, n)

    results: dict[str, list[int]] = {}
    barrier = threading.Barrier(4)

    def work(name: str) -> None:
        barrier.wait()
        with session_scope(factory) as s:
            claimed = repo.claim_jobs(
                s, status=JobStatus.DISCOVERED, limit=20, worker=name, lease_seconds=60
            )
            results[name] = [j.id for j in claimed]

    threads = [threading.Thread(target=work, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    all_ids = [i for ids in results.values() for i in ids]
    assert len(all_ids) == 20
    assert len(set(all_ids)) == 20


# --- caps ---------------------------------------------------------------------------------


def test_count_sent_today_counts_only_todays_utc_sends_on_channel(session: Session) -> None:
    today = NOW.replace(hour=0, minute=0)
    yesterday_late = today - timedelta(seconds=1)
    # Counted: sent today (including one that has since been replied to), in-flight today.
    make_application(session, make_job(session, 1), status=ApplicationStatus.SENT, sent_at=today)
    make_application(session, make_job(session, 2), status=ApplicationStatus.REPLIED, sent_at=NOW)
    make_application(
        session, make_job(session, 3), status=ApplicationStatus.SENDING, updated_at=NOW
    )
    # Not counted: yesterday, other channel, never sent, stale `sending`.
    make_application(
        session, make_job(session, 4), status=ApplicationStatus.SENT, sent_at=yesterday_late
    )
    make_application(
        session,
        make_job(session, 5),
        channel=Channel.PORTAL,
        status=ApplicationStatus.SENT,
        sent_at=NOW,
    )
    make_application(session, make_job(session, 6), status=ApplicationStatus.QUEUED)
    make_application(
        session, make_job(session, 7), status=ApplicationStatus.SENDING, updated_at=yesterday_late
    )

    assert repo.count_sent_today(session, Channel.EMAIL, now=NOW) == 3
    assert repo.count_sent_today(session, Channel.PORTAL, now=NOW) == 1
    assert repo.count_sent_today(session, Channel.LINKEDIN_DM, now=NOW) == 0


# --- channels -----------------------------------------------------------------------------


def test_pause_and_resume_channel_are_idempotent_and_audited(session: Session) -> None:
    assert repo.is_channel_paused(session, Channel.EMAIL) is False
    assert repo.pause_channel(session, Channel.EMAIL, "captcha", actor="w1") is True
    assert repo.pause_channel(session, Channel.EMAIL, "captcha again", actor="w1") is False
    assert repo.is_channel_paused(session, Channel.EMAIL) is True
    assert repo.is_channel_paused(session, Channel.PORTAL) is False

    assert repo.resume_channel(session, Channel.EMAIL, actor="user") is True
    assert repo.resume_channel(session, Channel.EMAIL, actor="user") is False
    assert repo.is_channel_paused(session, Channel.EMAIL) is False

    events = (
        session.execute(
            select(Event).where(Event.entity_type == EntityType.CHANNEL).order_by(Event.id)
        )
        .scalars()
        .all()
    )
    assert [e.action for e in events] == ["paused", "resumed"]
    assert events[0].payload == {"reason": "captcha"}


def test_record_event_writes_row(session: Session) -> None:
    event = repo.record_event(
        session, entity_type=EntityType.JOB, entity_id=7, action="note", actor="user"
    )
    assert event.id is not None
    assert event.entity_id == "7"


def test_summary_counts(session: Session) -> None:
    make_job(session, 1, JobStatus.SCORED)
    make_job(session, 2, JobStatus.SCORED)
    assert repo.row_counts(session)["jobs"] == 2
    assert repo.status_counts(session, Job) == {"scored": 2}
