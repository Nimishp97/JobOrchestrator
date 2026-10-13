"""State machines for Job and Application. Transition rules are data, not if-statements."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from jobagent.db import repo
from jobagent.db.models import (
    Application,
    ApplicationStatus,
    EntityType,
    Job,
    JobStatus,
    utcnow,
)

J = JobStatus
A = ApplicationStatus

JOB_TRANSITIONS: Mapping[JobStatus, frozenset[JobStatus]] = {
    J.DISCOVERED: frozenset({J.SCORED, J.FAILED}),
    J.SCORED: frozenset({J.SHORTLISTED, J.SKIPPED, J.FAILED}),
    J.SHORTLISTED: frozenset({J.APPLIED, J.FAILED}),
    J.SKIPPED: frozenset({J.SHORTLISTED}),  # manual override
    J.APPLIED: frozenset(),  # terminal
    J.FAILED: frozenset({J.DISCOVERED, J.SCORED, J.SHORTLISTED}),  # manual retry
}

APPLICATION_TRANSITIONS: Mapping[ApplicationStatus, frozenset[ApplicationStatus]] = {
    A.DRAFTED: frozenset({A.QUEUED, A.FAILED, A.WITHDRAWN}),
    A.QUEUED: frozenset({A.SENDING, A.FAILED, A.WITHDRAWN}),
    # `sending` is committed before the external call. A row stuck here is never auto-retried.
    A.SENDING: frozenset({A.SENT, A.FAILED, A.WITHDRAWN}),
    A.SENT: frozenset({A.REPLIED, A.INTERVIEW, A.REJECTED, A.NO_RESPONSE, A.WITHDRAWN}),
    A.REPLIED: frozenset({A.INTERVIEW, A.REJECTED, A.WITHDRAWN}),
    A.INTERVIEW: frozenset({A.REJECTED, A.WITHDRAWN}),
    A.NO_RESPONSE: frozenset({A.REPLIED, A.WITHDRAWN}),  # late replies happen
    A.REJECTED: frozenset({A.WITHDRAWN}),
    A.FAILED: frozenset({A.QUEUED, A.WITHDRAWN}),  # retry
    A.WITHDRAWN: frozenset(),  # terminal
}


class IllegalTransition(Exception):
    def __init__(self, entity_type: EntityType, from_status: str, to_status: str) -> None:
        super().__init__(f"illegal {entity_type} transition: {from_status} -> {to_status}")
        self.entity_type = entity_type
        self.from_status = from_status
        self.to_status = to_status


class ConcurrentModification(Exception):
    """The row's status changed between our read and our write."""


def _transition(
    session: Session,
    entity: Job | Application,
    *,
    entity_type: EntityType,
    rules: Mapping[Any, frozenset[Any]],
    to_status: Any,
    actor: str,
    payload: dict[str, Any] | None,
    extra_values: Mapping[str, Any],
    now: datetime,
) -> bool:
    from_status = entity.status
    if from_status == to_status:
        return False
    if to_status not in rules[from_status]:
        raise IllegalTransition(entity_type, str(from_status), str(to_status))

    model = type(entity)
    # Guarded on the status we validated against: a stale entity can never clobber a newer state.
    result = session.execute(
        update(model)
        .where(model.id == entity.id, model.status == from_status)
        .values(status=to_status, updated_at=now, **extra_values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise ConcurrentModification(f"{entity_type} {entity.id} is no longer {from_status}")
    session.refresh(entity)
    repo.record_event(
        session,
        entity_type=entity_type,
        entity_id=entity.id,
        action="status_changed",
        from_status=str(from_status),
        to_status=str(to_status),
        payload=payload,
        actor=actor,
        now=now,
    )
    return True


def transition_job(
    session: Session,
    job: Job,
    to_status: JobStatus,
    actor: str,
    payload: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """Move a job to `to_status` and write the audit event in the same transaction.

    Returns True if the status changed, False for a same-state request (a no-op, no event).
    Any lease is cleared, since leases are not stage-specific.
    """
    return _transition(
        session,
        job,
        entity_type=EntityType.JOB,
        rules=JOB_TRANSITIONS,
        to_status=to_status,
        actor=actor,
        payload=payload,
        extra_values={"claimed_by": None, "claimed_until": None},
        now=now or utcnow(),
    )


def transition_application(
    session: Session,
    application: Application,
    to_status: ApplicationStatus,
    actor: str,
    payload: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """Same contract as `transition_job`; entering `sent` also stamps `sent_at`."""
    now = now or utcnow()
    extra = {"sent_at": now} if to_status == ApplicationStatus.SENT else {}
    return _transition(
        session,
        application,
        entity_type=EntityType.APPLICATION,
        rules=APPLICATION_TRANSITIONS,
        to_status=to_status,
        actor=actor,
        payload=payload,
        extra_values=extra,
        now=now,
    )
