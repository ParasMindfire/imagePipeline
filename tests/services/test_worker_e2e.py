"""Worker processes a job end-to-end (the assignment's third required
test case), plus direct tests of the exactly-once mechanics themselves
— the claim race and the attempts-fencing token (DESIGN.md §2.4 /
DESIGN_QA.md Q16) — since those are the actual point of this system and
deserve more than documentation.
"""
from datetime import datetime, timedelta, timezone

from app.repositories import job_repo
from app.worker import process_job

# Known-good values from an actual run of scripts/generate_sample_images.py
# + app/services/blur_service.py — see the conversation's verification
# pass. If these ever drift, either the generator or opencv-python-headless
# changed, and that's exactly the determinism guarantee DESIGN.md is about.
SHARP_SCORE = 13738.2246


def test_worker_processes_job_end_to_end(db_session, sample_image_path):
    job = job_repo.create_job(db_session, image_path=sample_image_path, run_at=None)
    assert job.status == "pending"
    assert job.attempts == 0

    process_job(job.id)  # runs against its own, separate DB session — see note below

    # process_job() opens its own SessionLocal() internally (that's the
    # correct, real-world shape: a separate worker process never shares
    # a session with the API). db_session still has `job` cached from
    # creation, and SQLAlchemy won't know it's stale on its own —
    # expire_all() forces the next read to hit the DB for real instead
    # of silently returning pre-processing data.
    db_session.expire_all()
    updated = job_repo.get_job(db_session, job.id)
    assert updated.status == "done"
    assert updated.attempts == 1  # exactly one successful attempt — the assignment's core requirement
    assert updated.result == {"is_blurry": False, "sharpness_score": SHARP_SCORE}


def test_worker_processes_blurry_image_correctly(db_session, sample_blurry_image_path):
    job = job_repo.create_job(db_session, image_path=sample_blurry_image_path, run_at=None)
    process_job(job.id)
    db_session.expire_all()
    updated = job_repo.get_job(db_session, job.id)
    assert updated.status == "done"
    assert updated.result["is_blurry"] is True


def test_worker_result_is_deterministic_across_runs(db_session, sample_image_path):
    job_a = job_repo.create_job(db_session, image_path=sample_image_path, run_at=None)
    job_b = job_repo.create_job(db_session, image_path=sample_image_path, run_at=None)
    process_job(job_a.id)
    process_job(job_b.id)
    db_session.expire_all()
    a = job_repo.get_job(db_session, job_a.id)
    b = job_repo.get_job(db_session, job_b.id)
    assert a.result == b.result == {"is_blurry": False, "sharpness_score": SHARP_SCORE}


def test_worker_marks_missing_file_as_failed_or_retrying(db_session):
    job = job_repo.create_job(db_session, image_path="does_not_exist.png", run_at=None)
    process_job(job.id)
    db_session.expire_all()
    updated = job_repo.get_job(db_session, job.id)
    # First failure with MAX_RETRIES=3 (default) retries rather than
    # failing outright — DESIGN.md §6.
    assert updated.status == "pending"
    assert updated.attempts == 1
    assert updated.run_at is not None
    assert updated.enqueued is False


def test_job_fails_permanently_after_max_retries(db_session):
    job = job_repo.create_job(db_session, image_path="does_not_exist.png", run_at=None)

    for _ in range(3):  # MAX_RETRIES default
        process_job(job.id)
        db_session.expire_all()
        current = job_repo.get_job(db_session, job.id)
        if current.status == "failed":
            break
        # Still pending (retry-scheduled) — force it eligible immediately
        # instead of waiting out the real backoff, fenced on the
        # attempts value we just (freshly) read.
        job_repo.write_retry(db_session, job.id, current.attempts, datetime.now(timezone.utc))
        db_session.expire_all()

    db_session.expire_all()
    updated = job_repo.get_job(db_session, job.id)
    assert updated.status == "failed"
    assert updated.attempts == 3
    assert updated.result["error"]


def test_claim_is_atomic_only_one_winner(db_session, sample_image_path):
    """DESIGN.md 'Exactly-once processing' — direct test of the claim UPDATE."""
    job = job_repo.create_job(db_session, image_path=sample_image_path, run_at=None)

    first = job_repo.claim_job(db_session, job.id)
    second = job_repo.claim_job(db_session, job.id)  # simulates a duplicate delivery

    assert first is not None
    assert first.attempts == 1
    assert second is None  # the second "worker" gets 0 rows, per DESIGN.md §3


def test_terminal_write_is_fenced_by_attempts(db_session, sample_image_path):
    """DESIGN.md §2.4 / DESIGN_QA.md Q16 — the zombie-worker scenario,
    reproduced directly instead of only documented."""
    job = job_repo.create_job(db_session, image_path=sample_image_path, run_at=None)

    worker_a = job_repo.claim_job(db_session, job.id)  # attempts=1, "worker A"'s generation
    assert worker_a.attempts == 1

    # Simulate the reconciler reclaiming worker A's job (it went silent)
    # and worker B claiming it fresh. -1s: everything currently
    # "processing" is immediately stale. max_attempts=3: attempts=1 is
    # well under the cap, so this is the "reclaim for another try" path,
    # not the "give up" path (that's covered separately below).
    job_repo.reclaim_stale(db_session, timeout_seconds=-1, max_attempts=3)
    worker_b = job_repo.claim_job(db_session, job.id)  # attempts=2, "worker B"'s generation
    assert worker_b.attempts == 2

    # Worker B finishes correctly.
    ok_b = job_repo.write_terminal(db_session, job.id, attempts=2, status="done", result={"correct": True})
    assert ok_b is True

    # Worker A resurfaces and tries to report its now-stale result —
    # must be rejected, not silently overwrite B's result.
    ok_a = job_repo.write_terminal(db_session, job.id, attempts=1, status="done", result={"stale": True})
    assert ok_a is False

    final = job_repo.get_job(db_session, job.id)
    assert final.result == {"correct": True}  # B's result survives, A's is discarded


def test_reclaim_gives_up_after_max_attempts(db_session, sample_image_path):
    """EDGECASE.md 3.4 — a job that crashes/hangs a worker every single
    time must not be reclaimed and retried forever. Once attempts has
    already reached max_attempts, reclaim_stale must mark it `failed`
    instead of resetting it back to `pending` yet again."""
    job = job_repo.create_job(db_session, image_path=sample_image_path, run_at=None)

    # Two claim+reclaim cycles, each still under the cap, pushing
    # attempts to 2 while resetting back to `pending` each time
    # (simulating 2 prior crashes/hangs, each reclaimed for another try).
    for _ in range(2):
        job_repo.claim_job(db_session, job.id)
        job_repo.reclaim_stale(db_session, timeout_seconds=-1, max_attempts=3)

    # One more claim pushes attempts to 3 == max_attempts, while the row
    # is `processing`. The give-up threshold is ">=", so this next
    # reclaim sweep must give up immediately — not require attempts to
    # exceed the cap first.
    claimed = job_repo.claim_job(db_session, job.id)
    assert claimed.attempts == 3

    touched = job_repo.reclaim_stale(db_session, timeout_seconds=-1, max_attempts=3)
    assert touched == 1
    final = job_repo.get_job(db_session, job.id)
    assert final.status == "failed"
    assert "exceeded max attempts" in final.result["error"]
