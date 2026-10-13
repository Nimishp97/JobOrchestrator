"""Repository functions: the only place workers touch the database."""

import hashlib
import re
import unicodedata
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from jobagent.db.models import (
    Application,
    ApplicationStatus,
    Base,
    Channel,
    ChannelState,
    EntityType,
    Event,
    Job,
    JobStatus,
    LLMCall,
    LLMCallStatus,
    utcnow,
)

DEDUPE_VERSION = "v1"
_LEGAL_SUFFIXES = frozenset(
    {"inc", "llc", "ltd", "limited", "pvt", "private", "corp", "corporation", "gmbh", "co"}
)
_TRACKING_PARAMS = frozenset({"gclid", "fbclid", "ref", "refid", "trk", "trackingid", "source"})


# --- normalization and keys ---------------------------------------------------------------


def normalize_text(value: str) -> str:
    """NFKC, casefold, punctuation to spaces, collapsed whitespace."""
    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", folded).split())


def normalize_company(value: str) -> str:
    words = normalize_text(value).split()
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def canonicalize_url(url: str) -> str:
    """Lowercase scheme/host, drop fragment, tracking params and trailing slash."""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def make_dedupe_key(company: str, title: str, location: str | None) -> str:
    raw = "|".join(
        [
            DEDUPE_VERSION,
            normalize_company(company),
            normalize_text(title),
            normalize_text(location or ""),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# --- events -------------------------------------------------------------------------------


def record_event(
    session: Session,
    *,
    entity_type: EntityType,
    entity_id: int | str,
    action: str,
    actor: str,
    from_status: str | None = None,
    to_status: str | None = None,
    payload: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Event:
    event = Event(
        ts=now or utcnow(),
        entity_type=entity_type,
        entity_id=str(entity_id),
        action=action,
        from_status=from_status,
        to_status=to_status,
        payload=payload,
        actor=actor,
    )
    session.add(event)
    session.flush()
    return event


# --- jobs ---------------------------------------------------------------------------------


def upsert_job_if_new(
    session: Session,
    *,
    source: str,
    source_job_id: str,
    url: str,
    company: str,
    title: str,
    jd_text: str,
    location: str | None = None,
    actor: str = "system",
    now: datetime | None = None,
) -> tuple[Job, bool]:
    """Insert a job unless it already exists. Returns (job, created).

    A single INSERT ... ON CONFLICT DO NOTHING handles both unique constraints (dedupe_key and
    (source, source_job_id)) atomically, so concurrent discoverers cannot create duplicates.
    """
    dedupe_key = make_dedupe_key(company, title, location)
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    stmt = (
        insert(Job)
        .values(
            source=source,
            source_job_id=source_job_id,
            url=url,
            canonical_url=canonicalize_url(url),
            company=company,
            title=title,
            location=location,
            jd_text=jd_text,
            jd_hash=hash_text(jd_text),
            dedupe_key=dedupe_key,
            status=JobStatus.DISCOVERED,
            discovered_at=now or utcnow(),
            updated_at=now or utcnow(),
        )
        .on_conflict_do_nothing()
        .returning(Job.id)
    )
    new_id = session.execute(stmt).scalar_one_or_none()
    if new_id is not None:
        job = session.get_one(Job, new_id)
        record_event(
            session,
            entity_type=EntityType.JOB,
            entity_id=job.id,
            action="created",
            to_status=JobStatus.DISCOVERED,
            actor=actor,
            now=now,
        )
        return job, True

    existing = session.execute(
        select(Job).where(
            or_(
                Job.dedupe_key == dedupe_key,
                and_(Job.source == source, Job.source_job_id == source_job_id),
            )
        )
    ).scalar_one()
    return existing, False


def claim_jobs(
    session: Session,
    *,
    status: JobStatus,
    limit: int,
    worker: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> list[Job]:
    """Atomically lease up to `limit` jobs in `status` that have no live lease.

    One UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING id: the select and
    the lease write happen in a single statement, so two workers never get the same row.
    Expired leases match the predicate and are reclaimed (crash recovery).
    """
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    now = now or utcnow()
    candidates = (
        select(Job.id)
        .where(Job.status == status, or_(Job.claimed_until.is_(None), Job.claimed_until < now))
        .order_by(Job.id)
        .limit(limit)
        # Postgres: without this two workers can pick the same rows (the subquery is not re-run
        # after a concurrent update). SQLite ignores it; its writers are already serialized.
        .with_for_update(skip_locked=True)
    )
    claimed_ids = session.scalars(
        update(Job)
        .where(Job.id.in_(candidates))
        .values(claimed_by=worker, claimed_until=now + timedelta(seconds=lease_seconds))
        .returning(Job.id)
        .execution_options(synchronize_session=False)
    ).all()
    if not claimed_ids:
        return []
    return list(
        session.scalars(
            select(Job)
            .where(Job.id.in_(claimed_ids))
            .order_by(Job.id)
            .execution_options(populate_existing=True)
        )
    )


def release_claim(session: Session, job: Job, worker: str) -> bool:
    """Give a job back without advancing it. Only the current holder can release."""
    result = session.execute(
        update(Job)
        .where(Job.id == job.id, Job.claimed_by == worker)
        .values(claimed_by=None, claimed_until=None)
        .execution_options(synchronize_session=False)
    )
    session.refresh(job)
    return result.rowcount == 1  # type: ignore[attr-defined]


# --- applications -------------------------------------------------------------------------


def count_sent_today(session: Session, channel: Channel, now: datetime | None = None) -> int:
    """Applications on `channel` that went out (or may have) during the current UTC day.

    Counts by `sent_at`, so a sent application that later got a reply still counts. Rows stuck in
    `sending` are counted too: they may have gone out, and a cap must err on the safe side.
    """
    now = now or utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    stmt = (
        select(func.count())
        .select_from(Application)
        .where(
            Application.channel == channel,
            or_(
                and_(Application.sent_at >= day_start, Application.sent_at < day_end),
                and_(
                    Application.status == ApplicationStatus.SENDING,
                    Application.updated_at >= day_start,
                    Application.updated_at < day_end,
                ),
            ),
        )
    )
    return session.execute(stmt).scalar_one()


# --- channels -----------------------------------------------------------------------------


def is_channel_paused(session: Session, channel: Channel) -> bool:
    state = session.get(ChannelState, channel)
    return state is not None and state.paused


def pause_channel(
    session: Session, channel: Channel, reason: str, actor: str, now: datetime | None = None
) -> bool:
    """Pause a channel. Returns False (and writes no event) if it was already paused."""
    now = now or utcnow()
    state = session.get(ChannelState, channel)
    if state is not None and state.paused:
        return False
    if state is None:
        state = ChannelState(channel=channel)
        session.add(state)
    state.paused, state.paused_reason, state.paused_at = True, reason, now
    session.flush()
    record_event(
        session,
        entity_type=EntityType.CHANNEL,
        entity_id=channel,
        action="paused",
        from_status="active",
        to_status="paused",
        payload={"reason": reason},
        actor=actor,
        now=now,
    )
    return True


def resume_channel(
    session: Session, channel: Channel, actor: str, now: datetime | None = None
) -> bool:
    """Resume a channel. Returns False (and writes no event) if it was not paused."""
    state = session.get(ChannelState, channel)
    if state is None or not state.paused:
        return False
    state.paused, state.paused_reason, state.paused_at = False, None, None
    session.flush()
    record_event(
        session,
        entity_type=EntityType.CHANNEL,
        entity_id=channel,
        action="resumed",
        from_status="paused",
        to_status="active",
        actor=actor,
        now=now,
    )
    return True


# --- llm calls ----------------------------------------------------------------------------


def record_llm_call(
    session: Session,
    *,
    task: str,
    model: str,
    prompt_version: str,
    prompt_hash: str,
    status: LLMCallStatus,
    attempts: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    latency_ms: int = 0,
    error_type: str | None = None,
    cost_usd: Decimal | None = None,
    job_id: int | None = None,
    application_id: int | None = None,
    now: datetime | None = None,
) -> LLMCall:
    row = LLMCall(
        ts=now or utcnow(),
        task=task,
        model=model,
        prompt_version=prompt_version,
        prompt_hash=prompt_hash,
        status=status,
        attempts=attempts,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        latency_ms=latency_ms,
        error_type=error_type,
        cost_usd=cost_usd,
        job_id=job_id,
        application_id=application_id,
    )
    session.add(row)
    session.flush()
    return row


def llm_usage_today(session: Session, now: datetime | None = None) -> dict[str, Any]:
    """Totals for the current UTC day over calls that actually made a request (attempts > 0).

    Rows with an unknown price (cost_usd NULL) add to `calls` and tokens but not to `cost_usd`;
    `unpriced_calls` says how many, so callers can tell the cost is a lower bound.
    """
    day_start = datetime.combine((now or utcnow()).astimezone(UTC).date(), time.min, tzinfo=UTC)
    row = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(LLMCall.input_tokens), 0),
            func.coalesce(func.sum(LLMCall.output_tokens), 0),
            func.coalesce(func.sum(LLMCall.cost_usd), 0),
            func.count().filter(LLMCall.cost_usd.is_(None)),
        ).where(LLMCall.ts >= day_start, LLMCall.attempts > 0)
    ).one()
    return {
        "calls": row[0],
        "input_tokens": int(row[1]),
        "output_tokens": int(row[2]),
        "cost_usd": Decimal(str(row[3])),
        "unpriced_calls": row[4],
    }


# --- reporting ----------------------------------------------------------------------------


def row_counts(session: Session) -> dict[str, int]:
    return {
        table.name: session.execute(select(func.count()).select_from(table)).scalar_one()
        for table in Base.metadata.sorted_tables
    }


def status_counts(session: Session, model: type[Job] | type[Application]) -> dict[str, int]:
    rows = session.execute(select(model.status, func.count()).group_by(model.status)).all()
    return {str(status): count for status, count in rows}
