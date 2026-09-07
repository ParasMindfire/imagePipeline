"""The `jobs` table."""
import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class JobStatus(str, PyEnum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"


def _uuid_str() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    image_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=JobStatus.pending.value, index=True)

    # Counter AND fencing token — see DESIGN.md "Exactly-once processing".
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Gates claim eligibility for both scheduled jobs and retry backoff.
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # True only once a RabbitMQ message has actually been published
    # for this row's *current* pending state — see DESIGN.md §5 (the
    # reconciler) / Appendix.
    enqueued: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover — debugging aid only
        return f"<Job {self.id} status={self.status} attempts={self.attempts}>"
