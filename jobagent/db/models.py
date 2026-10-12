"""ORM models. Status sets are closed enums stored as VARCHAR + CHECK constraint."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(constraint_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes. SQLite drops tzinfo, so enforce it on both ends."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; use a timezone-aware UTC datetime")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class JobStatus(StrEnum):
    DISCOVERED = "discovered"
    SCORED = "scored"
    SHORTLISTED = "shortlisted"
    SKIPPED = "skipped"
    APPLIED = "applied"
    FAILED = "failed"


class ApplicationStatus(StrEnum):
    DRAFTED = "drafted"
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    REPLIED = "replied"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    NO_RESPONSE = "no_response"
    FAILED = "failed"
    WITHDRAWN = "withdrawn"


class Channel(StrEnum):
    EMAIL = "email"
    PORTAL = "portal"
    LINKEDIN_DM = "linkedin_dm"


class EntityType(StrEnum):
    JOB = "job"
    APPLICATION = "application"
    CHANNEL = "channel"


def _enum(cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="dedupe_key"),
        UniqueConstraint("source", "source_job_id", name="source_job_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64))
    source_job_id: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text)
    company: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    jd_text: Mapped[str] = mapped_column(Text)
    jd_hash: Mapped[str] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(64))

    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus, "job_status"), default=JobStatus.DISCOVERED, index=True
    )
    score: Mapped[int | None]
    score_reasons: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    skip_reason: Mapped[str | None] = mapped_column(Text)

    discovered_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    claimed_by: Mapped[str | None] = mapped_column(String(64))
    claimed_until: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("job_id", "channel", name="job_channel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"))
    channel: Mapped[Channel] = mapped_column(_enum(Channel, "channel"))
    status: Mapped[ApplicationStatus] = mapped_column(
        _enum(ApplicationStatus, "application_status"),
        default=ApplicationStatus.DRAFTED,
        index=True,
    )

    resume_path: Mapped[str | None] = mapped_column(Text)
    resume_hash: Mapped[str | None] = mapped_column(String(64))
    cover_note: Mapped[str | None] = mapped_column(Text)
    recipient: Mapped[str | None] = mapped_column(String(255))
    external_message_id: Mapped[str | None] = mapped_column(String(255))

    attempt_count: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Event(Base):
    """Append-only audit log. Updates and deletes are rejected by DB triggers."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    entity_type: Mapped[EntityType] = mapped_column(_enum(EntityType, "entity_type"))
    entity_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(64))


class ChannelState(Base):
    """One row per channel that has ever been paused. No row means not paused."""

    __tablename__ = "channel_state"

    channel: Mapped[Channel] = mapped_column(_enum(Channel, "channel"), primary_key=True)
    paused: Mapped[bool] = mapped_column(default=False)
    paused_reason: Mapped[str | None] = mapped_column(Text)
    paused_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
