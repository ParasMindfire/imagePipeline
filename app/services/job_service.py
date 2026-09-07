"""Business logic layer — sits between the API and the repository.

Path validation here guards against path traversal: image_path is
always resolved against IMAGE_BASE_DIR and rejected if it would escape
that directory.
"""
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.queue import publish_job_standalone
from app.repositories import job_repo

settings = get_settings()


class InvalidImagePathError(ValueError):
    pass


def resolve_image_path(image_path: str) -> Path:
    """Resolve image_path against IMAGE_BASE_DIR and reject anything that
    would escape it (../../etc/passwd, an absolute path elsewhere, a
    symlink pointing out)."""
    base = Path(settings.IMAGE_BASE_DIR).resolve()
    candidate = (base / image_path).resolve()
    if candidate != base and base not in candidate.parents:
        raise InvalidImagePathError(f"image_path escapes the allowed directory: {image_path}")
    return candidate


def create_job(db: Session, image_path: str, run_at: datetime | None):
    # Validate before writing anything — fail fast with a clean error
    # instead of letting the worker discover it later.
    resolve_image_path(image_path)

    job = job_repo.create_job(db, image_path=image_path, run_at=run_at)

    if run_at is None:
        # Fast path: publish immediately. If this fails (broker down, or
        # the process dies before the next line), enqueued stays False
        # and the reconciler's Sweep 2 picks it up later — DESIGN.md §4.
        try:
            publish_job_standalone(job.id)
            job_repo.mark_enqueued(db, job.id)
        except Exception:
            # Swallow deliberately: the job row is already durable, and
            # "not yet enqueued" is a recoverable state, not something to
            # fail the request over — DESIGN.md §4 step 5.
            pass
    # else: run_at is in the future — deliberately left un-enqueued; the
    # reconciler's Sweep 2 publishes it once due (DESIGN.md §7).

    return job


def get_job(db: Session, job_id: str):
    return job_repo.get_job(db, job_id)


def list_jobs(db: Session, status: str | None, limit: int | None, offset: int | None):
    limit = min(limit or settings.DEFAULT_PAGE_SIZE, settings.MAX_PAGE_SIZE)
    offset = max(offset or 0, 0)
    return job_repo.list_jobs(db, status=status, limit=limit, offset=offset)
