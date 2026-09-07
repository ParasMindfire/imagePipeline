"""Background reconciler — DESIGN.md §5.

Runs as a thread inside every worker process; no separate service, no
leader election needed (DESIGN_QA.md Q8 — duplicate publishes from
multiple reconciler threads are harmless, the claim UPDATE in
job_repo.claim_job makes a duplicate a no-op).
"""
import logging
import threading

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.queue import declare_queue, get_connection, publish_job
from app.repositories import job_repo

settings = get_settings()
logger = logging.getLogger("reconciler")


def run_once() -> None:
    db = SessionLocal()
    try:
        reclaimed = job_repo.reclaim_stale(db, settings.PROCESSING_TIMEOUT_SECONDS, settings.MAX_RETRIES)
        if reclaimed:
            logger.info("reclaimed %d stale job(s)", reclaimed)

        due_ids = job_repo.find_due_unenqueued(db)
        if not due_ids:
            return

        connection = get_connection()
        try:
            channel = connection.channel()
            declare_queue(channel)
            for job_id in due_ids:
                publish_job(channel, job_id)
                job_repo.mark_enqueued(db, job_id)
            logger.info("republished %d job(s)", len(due_ids))
        finally:
            connection.close()
    finally:
        db.close()


def run_forever(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            run_once()
        except Exception:
            logger.exception("reconciler tick failed")
        stop_event.wait(settings.RECONCILER_INTERVAL_SECONDS)
