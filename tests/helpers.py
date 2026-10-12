from datetime import datetime

from sqlalchemy.orm import Session

from jobagent.db.models import Application, ApplicationStatus, Channel, Job, JobStatus, utcnow


def make_job(session: Session, n: int = 1, status: JobStatus = JobStatus.DISCOVERED) -> Job:
    job = Job(
        source="test",
        source_job_id=f"id-{n}",
        url=f"https://example.com/jobs/{n}",
        canonical_url=f"https://example.com/jobs/{n}",
        company=f"Company {n}",
        title="Engineer",
        jd_text="jd",
        jd_hash="hash",
        dedupe_key=f"key-{n}",
        status=status,
    )
    session.add(job)
    session.flush()
    return job


def make_application(
    session: Session,
    job: Job,
    channel: Channel = Channel.EMAIL,
    status: ApplicationStatus = ApplicationStatus.DRAFTED,
    sent_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> Application:
    application = Application(
        job_id=job.id,
        channel=channel,
        status=status,
        sent_at=sent_at,
        updated_at=updated_at or utcnow(),
    )
    session.add(application)
    session.flush()
    return application
