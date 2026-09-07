# EDGECASE.md — everything wrong with the naive version of this system

This is the adversarial pass. Assume every component is malicious,
broken, slow, or lying, and assume the reviewer in the follow-up
interview will ask "ok but what if—" about literally every line. Each
item: **scenario → why it actually breaks → where it's actually
implemented** (file:line), or an explicit note that it's an accepted,
documented gap with no code behind it.

---

## 1. Input / API layer

| # | Scenario | Why it breaks | Where implemented |
|---|---|---|---|
| 1.1 | `image_path` is missing / empty string | Naive `open(path)` throws before you've even created a job row — inconsistent error surface | `app/schemas/job.py:9` — `Field(..., min_length=1, max_length=1024)`. Pydantic rejects an empty string with a clean `422` before any DB write. |
| 1.2 | `image_path` is a path traversal (`../../../etc/passwd`) or an absolute path to something sensitive on the host | A "blurry image detector" becomes an arbitrary file reader if left unchecked | `app/services/job_service.py:23-31` — `resolve_image_path()` resolves against `IMAGE_BASE_DIR` and rejects anything that escapes it. Called at creation time (`job_service.py:37`, inside `create_job()`) **and again** at claim time (`app/worker.py`, inside `process_job()`) — defense in depth, not trusting the DB blindly the second time. Tested directly: `tests/api/test_jobs_api.py::test_create_job_rejects_path_traversal`. |
| 1.3 | `image_path` points at a directory, a device file, a named pipe, or a symlink to one | `cv2.imread` on a directory returns `None` silently; a pipe/device can hang the read forever | `app/services/blur_service.py:31` — `if os.path.islink(path) or not os.path.isfile(path): raise UnreadableImageError(...)`, checked *before* `cv2.imread` is ever called. |
| 1.4 | File exists but isn't a real image (renamed `.txt`, truncated JPEG, zero-byte file) | `cv2.imread` returns `None`, doesn't raise | `app/services/blur_service.py` — `if img is None: raise UnreadableImageError(...)`, immediately after the `cv2.imread` call. |
| 1.5 | Image is enormous (500MB TIFF, a decompression-bomb PNG) | Worker OOMs, job stuck in `processing` forever with no exception ever raised | `app/core/config.py:47` — `MAX_IMAGE_SIZE_BYTES = 20_000_000` (20MB, configurable). Enforced in `app/services/blur_service.py:36-38` — `os.path.getsize(path)` checked *before* decode. |
| 1.6 | Non-ASCII / very long path | Path handling bugs differ across OS/filesystems; some DB columns silently truncate | `app/schemas/job.py:9` — `max_length=1024`, matching `app/models/job.py:31` — `String(1024)` exactly. The API rejects an over-long path with a clean error before the DB could ever have a truncation surprise. |
| 1.7 | Client double-submits the same `POST /jobs` (retry after timeout, double-click) | No idempotency key → two full job rows, two full processing runs | **Not implemented — accepted, documented gap.** The assignment doesn't ask for idempotency keys; two identical submissions just become two independent job rows, each correctly processed once (not incorrect, just not deduplicated). |
| 1.8 | `GET /jobs?status=<anything>` bogus, or `?limit=999999999` | Unvalidated query params: bogus status either 500s or silently returns nothing; huge `limit` can blow the request | `app/api/v1/endpoints/jobs.py:42` — `status: JobStatus \| None = Query(...)`, FastAPI validates the enum, `422` on a bad value. `app/services/job_service.py:64` — `limit = min(limit or DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)`, with `MAX_PAGE_SIZE=100` at `app/core/config.py:51`. Tested: `tests/api/test_jobs_api.py::test_list_jobs_limit_is_capped_server_side`. |
| 1.9 | No rate limiting | Someone scripts 100k `POST /jobs` in a loop | **Not implemented — accepted, documented gap**, explicitly out of scope for this assignment, not a silent omission. |
| 1.10 | `GET /jobs/{id}` with a malformed UUID | Can throw a raw DB-driver exception that leaks a stack trace | `app/models/job.py:30` — `id` is a plain `String(36)`, not a native UUID column type. `app/repositories/job_repo.py:34` — `get_job()` is a plain PK lookup; a malformed id just matches nothing and returns `None`, which the endpoint turns into a clean `404` — the problem is sidestepped structurally rather than needing an explicit validator. |

---

## 2. Worker / processing layer

| # | Scenario | Why it breaks | Where implemented |
|---|---|---|---|
| 2.1 | Worker dies mid-processing, **after** the claim UPDATE but **before** the terminal write | Job stuck at `status='processing'` forever if nothing reclaims it | `app/repositories/job_repo.py:102` — `reclaim_stale()`, called every tick from `app/services/reconciler.py:23`. |
| 2.2 | Worker dies **after** committing `done`/`failed` but **before** acking | RabbitMQ redelivers; a second worker re-runs the claim | `app/repositories/job_repo.py:48` — `claim_job()`'s `WHERE status='pending'` makes the redelivered attempt a no-op (0 rows). Tested directly: `tests/services/test_worker_e2e.py::test_claim_is_atomic_only_one_winner`. |
| 2.3 | A message's `job_id` doesn't exist in the DB at all | Naive code throws `NoResultFound`, which can turn into an infinite requeue loop | `app/worker.py:59` — `process_job()`: `claim_job()` returns `None` for a nonexistent id exactly like an already-claimed one, so it's a no-op, never a requeue. The JSON-unparseable case is separately caught in `_process_message()` (`app/worker.py:109`) and ack-dropped. |
| 2.4 | Worker's DB connection drops mid-transaction (Postgres restart, network blip) | Job left in `processing` with no worker actually holding it | Reclaim (2.1) is the backstop. On top of that: `app/core/database.py:28` — `pool_pre_ping=True` (Postgres only) detects and replaces a dead connection on next checkout instead of erroring on it. |
| 2.5 | OpenCV segfaults on a malformed/adversarial image (native code, not a Python exception) | Kills the entire worker process; no `except Exception` catches a segfault | **Not implemented — accepted gap given scope.** No subprocess isolation for the decode step; reasonable since images come from a controlled directory, not untrusted uploads. Reclaim (2.1) is the backstop if it ever happens — the reconciler's `MAX_RETRIES` cap (3.4) prevents an unbounded churn loop even in that case. |
| 2.6 | Worker is alive but hung (infinite loop, huge image taking forever) | No automatic redelivery; RabbitMQ's own consumer-timeout default is 30 minutes | **Deliberate scope simplification, not a hard per-job timeout.** `app/core/config.py:25` — `PROCESSING_TIMEOUT_SECONDS=120` is the reconciler's static staleness threshold (`reclaim_stale()`), safe here because image processing is fast and bounded — see DESIGN.md §5 / DESIGN_QA.md for the reasoning. |
| 2.7 | `prefetch_count` not set — one worker slurps the whole backlog | Starves N-1 workers, defeats the point of running N of them | `app/worker.py:139` — `channel.basic_qos(prefetch_count=1)`. **Verified live** via the RabbitMQ management UI (all 3 channels showed `Prefetch: 1`). |
| 2.8 | Worker receives `SIGTERM` while a job is claimed | Ungraceful kill leaves the job in `processing`, in-flight work lost | `app/worker.py:127` — `signal.signal(signal.SIGTERM, _handle_signal)` + `_stop_event` (`app/worker.py:32`) + the `inactivity_timeout=1` consume loop (`app/worker.py:149`) that checks it every ~1s, letting the current job finish before exiting cleanly. |

---
