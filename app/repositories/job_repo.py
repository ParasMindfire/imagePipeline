"""Data-access layer — every exactly-once-relevant statement lives here,
matching the SQL in DESIGN.md exactly. If you're checking the code
against the docs, this is the file to look at.

Timestamps are computed in Python (`_utcnow()`), never with a SQL
`now()` literal, so the same code runs unchanged against Postgres in
Docker and SQLite in tests (DESIGN.md "Assumptions").
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.job import Job, JobStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_job(db: Session, image_path: str, run_at: datetime | None) -> Job:
    job = Job(image_path=image_path, status=JobStatus.pending.value, run_at=run_at, enqueued=False)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def mark_enqueued(db: Session, job_id: str) -> None:
    db.execute(update(Job).where(Job.id == job_id).values(enqueued=True))
    db.commit()


def get_job(db: Session, job_id: str) -> Job | None:
    return db.get(Job, job_id)


def list_jobs(db: Session, status: str | None, limit: int, offset: int) -> tuple[list[Job], int]:
    stmt = select(Job)
    if status is not None:
        stmt = stmt.where(Job.status == status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    stmt = stmt.order_by(Job.created_at.asc()).limit(limit).offset(offset)
    items = list(db.execute(stmt).scalars().all())
    return items, total


def claim_job(db: Session, job_id: str):
    """The atomic claim — 'Exactly-once processing'.

    Returns a Row with .id/.attempts/.image_path on success, or None if
    the job was already claimed (or done, failed, or not yet due) by
    the time this ran — the caller must ack-and-discard on None, never
    retry or reprocess.
    """
    now = _utcnow()
    stmt = (
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == JobStatus.pending.value,
            (Job.run_at.is_(None)) | (Job.run_at <= now),
        )
        .values(status=JobStatus.processing.value, attempts=Job.attempts + 1, updated_at=now)
        .returning(Job.id, Job.attempts, Job.image_path)
    )
    row = db.execute(stmt).first()
    db.commit()
    return row


def write_terminal(db: Session, job_id: str, attempts: int, status: str, result: dict) -> bool:
    """Fenced terminal write — the `attempts` fencing token.

    Conditioned on the exact `attempts` the caller claimed with, not
    just `id`. Returns False if a later claim already moved attempts
    past this value (the caller is a zombie and must discard its result
    rather than trust or retry it).
    """
    stmt = (
        update(Job)
        .where(Job.id == job_id, Job.attempts == attempts)
        .values(status=status, result=result, updated_at=_utcnow())
    )
    res = db.execute(stmt)
    db.commit()
    return res.rowcount == 1


def write_retry(db: Session, job_id: str, attempts: int, run_at: datetime) -> bool:
    """Fenced retry write . Same fencing as write_terminal."""
    stmt = (
        update(Job)
        .where(Job.id == job_id, Job.attempts == attempts)
        .values(status=JobStatus.pending.value, run_at=run_at, enqueued=False, updated_at=_utcnow())
    )
    res = db.execute(stmt)
    db.commit()
    return res.rowcount == 1


def reclaim_stale(db: Session, timeout_seconds: int, max_attempts: int) -> int:
    """Reconciler Sweep 1 — DESIGN.md §5. Returns how many rows it touched
    (reclaimed for another try, or given up on for good).

    A job that crashes (or segfaults) every single worker that ever
    touches it must not be reclaimed and
    retried forever — that's an unbounded churn loop, one crashed
    worker at a time. It needs the same MAX_RETRIES cap the explicit
    (caught-exception) failure path already has in worker.py's
    _handle_failure. This was missing before: reclaim used to
    unconditionally reset every stale row back to `pending` regardless
    of how many times it had already been reclaimed.
    """
    cutoff = _utcnow() - timedelta(seconds=timeout_seconds)
    now = _utcnow()

    give_up_stmt = (
        update(Job)
        .where(Job.status == JobStatus.processing.value, Job.updated_at < cutoff, Job.attempts >= max_attempts)
        .values(
            status=JobStatus.failed.value,
            result={"error": "exceeded max attempts — worker crashed/hung on this job too many times"},
            updated_at=now,
        )
    )
    gave_up = db.execute(give_up_stmt).rowcount

    reclaim_stmt = (
        update(Job)
        .where(Job.status == JobStatus.processing.value, Job.updated_at < cutoff, Job.attempts < max_attempts)
        .values(status=JobStatus.pending.value, run_at=None, enqueued=False, updated_at=now)
    )
    reclaimed = db.execute(reclaim_stmt).rowcount

    db.commit()
    return reclaimed + gave_up


def find_due_unenqueued(db: Session, limit: int = 200) -> list[str]:
    """Reconciler Sweep 2 (the SELECT half) — DESIGN.md §5."""
    now = _utcnow()
    stmt = (
        select(Job.id)
        .where(
            Job.status == JobStatus.pending.value,
            Job.enqueued.is_(False),
            (Job.run_at.is_(None)) | (Job.run_at <= now),
        )
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())
