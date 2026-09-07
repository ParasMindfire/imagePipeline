"""Worker entrypoint — `python -m app.worker`.

One job at a time per process . Scale by running more worker
containers (`docker compose up --scale worker=N`), not more threads
inside one.
"""
import json
import logging
import os
import signal
import threading
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.queue import declare_queue, get_connection
from app.repositories import job_repo
from app.services import reconciler
from app.services.blur_service import UnreadableImageError, analyze_image
from app.services.job_service import resolve_image_path

# Docker sets HOSTNAME to the container's short ID automatically, which
# makes it a free, already-unique per-worker identifier — no extra
# config needed to answer "which worker handled this job".
WORKER_ID = os.environ.get("HOSTNAME", "unknown")

logging.basicConfig(level=logging.INFO, format=f"%(asctime)s %(levelname)s %(name)s [worker={WORKER_ID}]: %(message)s")
logger = logging.getLogger("worker")
settings = get_settings()

_stop_event = threading.Event()


def _handle_signal(signum, frame) -> None:
    logger.info("received signal %s — finishing current job, then stopping", signum)
    # .set() here is a method name on threading.Event, and it means "raise this flag to True
    _stop_event.set()


def _handle_failure(db, job_id: str, attempts: int, error: str) -> None:
    if attempts < settings.MAX_RETRIES:
        # Exponential backoff — DESIGN.md §6: attempt 1 -> 2s, 2 -> 4s, 3 -> 8s.
        backoff = settings.RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempts - 1))
        run_at = datetime.now(timezone.utc) + timedelta(seconds=backoff)
        ok = job_repo.write_retry(db, job_id, attempts, run_at)
        if ok:
            logger.warning("job %s failed (attempt %d), retrying in %.0fs: %s", job_id, attempts, backoff, error)
        else:
            logger.info("job %s: retry write rejected — reclaimed by someone else in the meantime", job_id)
    else:
        ok = job_repo.write_terminal(db, job_id, attempts, "failed", {"error": error})
        if ok:
            logger.warning("job %s failed permanently after %d attempts: %s", job_id, attempts, error)
        else:
            logger.info("job %s: terminal write rejected — reclaimed by someone else in the meantime", job_id)


def process_job(job_id: str) -> None:
    """Claim -> blur-check -> write terminal state. No pika/channel
    dependency, so this is directly unit-testable (tests/services) —
    `_process_message` below is just the thin RabbitMQ-shaped wrapper
    around this."""
    db = SessionLocal()
    try:
        claimed = job_repo.claim_job(db, job_id)
        if claimed is None:
            # Already claimed, already terminal, or not due yet — someone
            # else has it, or will. No-op, per DESIGN.md's claim mechanics.
            return

        attempts = claimed.attempts
        try:
            path = resolve_image_path(claimed.image_path)
            result = analyze_image(str(path), settings.BLUR_THRESHOLD, settings.MAX_IMAGE_SIZE_BYTES)
            ok = job_repo.write_terminal(db, job_id, attempts, "done", result)
            # DESIGN.md §2.4 — a zombie worker's write can be silently
            # rejected by the attempts fence (someone else already
            # finished this job). The old version logged "done"
            # unconditionally here, which would misleadingly claim
            # success even when nothing was actually written.
            if ok:
                logger.info("job %s done (attempt %d): %s", job_id, attempts, result)
            else:
                logger.info("job %s: terminal write rejected — reclaimed by someone else in the meantime (result discarded)", job_id)
        except (UnreadableImageError, FileNotFoundError, OSError) as exc:
            _handle_failure(db, job_id, attempts, str(exc))
        except Exception as exc:  # noqa: BLE001 — any failure here must fail the *job*, never crash the worker
            logger.exception("unexpected error processing job %s", job_id)
            _handle_failure(db, job_id, attempts, str(exc))
    finally:
        db.close()


def _safe_ack(channel, delivery_tag) -> None:
    """Acking on a channel the broker already force-closed (e.g. a
    consumer-timeout case) throws. By the time this is called the job's
    outcome is already durably committed to Postgres, so a failed ack
    just means the broker may redeliver this message later — and that
    redelivery is already a safe no-op per the claim mechanics
    (DESIGN.md §3). Log it, don't crash the consumer loop over it."""
    try:
        channel.basic_ack(delivery_tag=delivery_tag)
    except Exception:
        logger.warning("ack failed (channel likely closed) — message may be redelivered, which is safe", exc_info=True)


def _process_message(channel, method, body: bytes) -> None:
    try:
        payload = json.loads(body)
        job_id = payload["job_id"]
    except Exception:
        # Poison message — not even parseable as a job. Ack and drop
        # rather than requeue-loop forever.
        logger.error("unparseable message, dropping: %r", body)
        _safe_ack(channel, method.delivery_tag)
        return

    process_job(job_id)
    _safe_ack(channel, method.delivery_tag)


def main() -> None:
    # These are OS-level signals 
    # If this process receives a SIGTERM signal, call _handle_signal(). This is exactly what docker stop, docker compose down send by default . Docker gives it a grace period (10s by default); if the process hasn't exited by then, Docker escalates to SIGKILL — which cannot be caught or handled at all, no cleanup possible, instant death. That's exactly why we bother catching SIGTERM here: it's our one chance to shut down gracefully instead of getting killed mid-job.
    signal.signal(signal.SIGTERM, _handle_signal)
    # sent when you press Ctrl+C in a terminal
    signal.signal(signal.SIGINT, _handle_signal)

    # Without daemon=True: main() finishes, but Python won't actually let the process exit, because that still-running reconciler thread (non-daemon by default) is keeping it alive.
    reconciler_thread = threading.Thread(target=reconciler.run_forever, args=(_stop_event,), daemon=True)
    reconciler_thread.start()

    connection = get_connection()
    channel = connection.channel()
    declare_queue(channel)
    # with prefetch_count=1 it says , gets one message wait untill ack send next message this avoids one worker getting all the jobs
    channel.basic_qos(prefetch_count=1)

    logger.info("worker started, waiting for jobs on %r", settings.QUEUE_NAME)

    # inactivity_timeout=1 forces this generator to yield every ~1s even
    # when the queue is empty, instead of blocking indefinitely — that's
    # what lets us check _stop_event on a bounded schedule (not relying on
    # SIGTERM reliably interrupting a blocked socket wait), so shutdown
    # is guaranteed within ~1s regardless of message flow (graceful SIGTERM shutdown).
    for method, properties, body in channel.consume(settings.QUEUE_NAME, inactivity_timeout=1):
        if _stop_event.is_set():
            break
        if method is None:
            continue
        _process_message(channel, method, body)

    logger.info("worker shutting down")
    channel.close()
    connection.close()


if __name__ == "__main__":
    main()
