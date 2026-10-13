"""initial schema

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHANNELS = ("email", "portal", "linkedin_dm")
JOB_STATUSES = ("discovered", "scored", "shortlisted", "skipped", "applied", "failed")
APPLICATION_STATUSES = (
    "drafted",
    "queued",
    "sending",
    "sent",
    "replied",
    "interview",
    "rejected",
    "no_response",
    "failed",
    "withdrawn",
)
ENTITY_TYPES = ("job", "application", "channel")


def _enum(name: str, values: tuple[str, ...]) -> sa.Enum:
    # The CHECK constraint is declared explicitly (and named) at table level below.
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=False, length=32)


def _check(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=op.f(name))


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_job_id", sa.String(length=255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("company", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("jd_text", sa.Text(), nullable=False),
        sa.Column("jd_hash", sa.String(length=64), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("status", _enum("job_status", JOB_STATUSES), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("score_reasons", sa.JSON(), nullable=True),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("discovered_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("claimed_by", sa.String(length=64), nullable=True),
        sa.Column("claimed_until", sa.DateTime(), nullable=True),
        _check("status", JOB_STATUSES, "ck_jobs_job_status"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_jobs_dedupe_key")),
        sa.UniqueConstraint("source", "source_job_id", name=op.f("uq_jobs_source_job_id")),
    )
    op.create_index(op.f("ix_jobs_status"), "jobs", ["status"])

    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("channel", _enum("channel", CHANNELS), nullable=False),
        sa.Column("status", _enum("application_status", APPLICATION_STATUSES), nullable=False),
        sa.Column("resume_path", sa.Text(), nullable=True),
        sa.Column("resume_hash", sa.String(length=64), nullable=True),
        sa.Column("cover_note", sa.Text(), nullable=True),
        sa.Column("recipient", sa.String(length=255), nullable=True),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        _check("channel", CHANNELS, "ck_applications_channel"),
        _check("status", APPLICATION_STATUSES, "ck_applications_application_status"),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_applications_job_id_jobs"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_applications")),
        sa.UniqueConstraint("job_id", "channel", name=op.f("uq_applications_job_channel")),
    )
    op.create_index(op.f("ix_applications_status"), "applications", ["status"])

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("entity_type", _enum("entity_type", ENTITY_TYPES), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=False),
        _check("entity_type", ENTITY_TYPES, "ck_events_entity_type"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    op.create_index(op.f("ix_events_entity"), "events", ["entity_type", "entity_id"])

    op.create_table(
        "channel_state",
        sa.Column("channel", _enum("channel", CHANNELS), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("paused_reason", sa.Text(), nullable=True),
        sa.Column("paused_at", sa.DateTime(), nullable=True),
        _check("channel", CHANNELS, "ck_channel_state_channel"),
        sa.PrimaryKeyConstraint("channel", name=op.f("pk_channel_state")),
    )

    # The audit log is append-only: enforce it in the database, not just in code.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE FUNCTION events_append_only() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'events is append-only'; END $$"
        )
        for action in ("update", "delete"):
            op.execute(
                f"CREATE TRIGGER events_no_{action} BEFORE {action.upper()} ON events "
                "FOR EACH ROW EXECUTE FUNCTION events_append_only()"
            )
        return
    for action in ("update", "delete"):
        op.execute(
            f"CREATE TRIGGER events_no_{action} BEFORE {action.upper()} ON events "
            "BEGIN SELECT RAISE(ABORT, 'events is append-only'); END"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER events_no_delete ON events")
        op.execute("DROP TRIGGER events_no_update ON events")
        op.execute("DROP FUNCTION events_append_only()")
    else:
        op.execute("DROP TRIGGER events_no_delete")
        op.execute("DROP TRIGGER events_no_update")
    op.drop_table("channel_state")
    op.drop_index(op.f("ix_events_entity"), table_name="events")
    op.drop_table("events")
    op.drop_index(op.f("ix_applications_status"), table_name="applications")
    op.drop_table("applications")
    op.drop_index(op.f("ix_jobs_status"), table_name="jobs")
    op.drop_table("jobs")
