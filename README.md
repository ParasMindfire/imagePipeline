# Image Job Processing Service

FastAPI accepts image jobs, RabbitMQ queues them, N worker containers
process them (blur detection via OpenCV), Postgres is the source of
truth. See [DESIGN.md](DESIGN.md) for how exactly-once processing is
guaranteed.

## Prerequisites

- Docker + Docker Compose (everything runs containerized — no local
  Python install needed just to *run* the service)
- Python 3.11+ locally only if you want to run `tests/`,
  `stress_test.py`, or `scripts/` outside Docker

## Setup

```bash
cp .env.example .env
pip install Pillow numpy   # only these two are needed to generate images — see below for the full dev install
python scripts/generate_sample_images.py
```
The generator needs `Pillow`/`numpy` locally even though nothing else
does — it runs on the host, producing files that get bind-mounted into
the containers, so it can't run *inside* Docker chicken-and-egg style.
If you're also going to run `tests/`, `stress_test.py`, or
`scripts/smoke_test.py` locally, just `pip install -r requirements.txt`
once instead (see "Run the tests" below) and skip the two-package line
above entirely.

The generator command produces `sample_images/` — 8 deterministic PNGs
(procedurally generated, not downloaded — see
`scripts/generate_sample_images.py` for why: no licensing questions,
no network dependency, byte-identical on every machine that runs it).
`sample_images/` is gitignored on purpose; if you received this repo


## Run everything

```bash
docker compose up --build -d
docker compose up --scale worker=3 -d   # 3 workers, per the assignment's example
```
That's the whole setup. **There is no manual database-creation step** —
worth spelling out since it's easy to expect one: the `postgres`
service creates its database/user automatically on first boot from the
`POSTGRES_*` variables in `.env`, and the `api` service's startup
command runs `alembic upgrade head` (creating the `jobs` table) before
`uvicorn` even starts — see it happen in `docker compose logs api`.
Healthchecks gate startup order, so the two commands above are all you
need, in that order, no manual waiting in between.

Scale workers up or down any time with `docker compose up --scale worker=N -d`.

## Try it

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"image_path": "checkerboard_sharp.png"}'
# {"id": "...", "status": "pending"}

curl http://localhost:8000/jobs/<id>
# {"id": "...", "status": "done", "result": {"is_blurry": false, "sharpness_score": 13738.2246}, "attempts": 1, ...}

curl "http://localhost:8000/jobs?status=done&limit=20&offset=0"
```

### Testing with Postman

Same three endpoints, set up as requests instead of curl commands:

**1. Create a job**
- Method: `POST`
- URL: `http://localhost:8000/jobs`
- Headers: `Content-Type: application/json`
- Body → raw → JSON:
  ```json
  {
    "image_path": "checkerboard_sharp.png"
  }
  ```
- Expect `201 Created`: `{"id": "...", "status": "pending"}`. Copy the `id` for the next request. (Any filename from `sample_images/` works — `checkerboard_blurry.png`, `noise_sharp.png`, `gradient_smooth.png`, etc. See `scripts/generate_sample_images.py` for the full list of 8.)
  ```json
  {
    "image_path": "checkerboard_sharp.png",
    "run_at": "2026-09-05T18:00:00Z"
  }
  ```

**2. Get a job's status**
- Method: `GET`
- URL: `http://localhost:8000/jobs/{id}` — replace `{id}` with the id from step 1
- No body needed
- Expect `200 OK` with `id`, `status`, `result`, `created_at`, `updated_at`, `attempts`. Re-send a few times right after creating one — you'll see `status` move from `pending` → `done` (usually well under a second).
- A made-up id returns `404`.

**3. List jobs (paginated, optional status filter)**
- Method: `GET`
- URL: `http://localhost:8000/jobs?status=done&limit=20&offset=0`
- Query params, all optional: `status` (`pending`/`processing`/`done`/`failed`), `limit` (capped at 100 server-side regardless of what's requested), `offset`
- Expect `200 OK` with `{"items": [...], "total": N, "limit": 20, "offset": 0}`

A bad `image_path` (e.g. `"../../etc/passwd"`) returns `400` on step 1 — that's the path-traversal guard rejecting it before a job is even created.

## Run the tests

Create the virtual environment once, then activate it (standard Python
workflow — do this once per terminal session):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# if PowerShell blocks this with an execution-policy error, run once:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```
On macOS/Linux (bash/zsh), activation is `source .venv/bin/activate` instead.
Once activated, plain commands work with no path prefix, same as any
other Python project:
```powershell
pip install -r requirements.txt
python scripts\generate_sample_images.py   # tests read real generated images
pytest tests\ -v
```

If your machine's Python is newer than 3.11 (e.g. 3.13/3.14), a couple
of the exact pinned versions (`pydantic-core`, `psycopg2-binary`) may
not have prebuilt wheels yet, and `pip install` will try to compile
them from source and fail without a Rust/MSVC toolchain. Docker never
hits this (its image is Python 3.11, which has wheels for every pin);
locally, `pip install --only-binary=:all: -r requirements.txt` works
around it.

**If you ever rename or move the `.venv` folder after installing into
it**, commands like plain `pytest` will fail with `Fatal error in
launcher: Unable to create process...` pointing at the *old* path —
pip bakes the interpreter's absolute path into each generated launcher
`.exe` (`pytest.exe`, `alembic.exe`, `uvicorn.exe`, etc.) at install
time, and renaming the folder doesn't update them. The fix is to
delete `.venv` and recreate it fresh at its final location — don't
create it under one name and rename it later.

If you don't want to activate at all, calling `.venv`'s `python.exe`
directly also works from any shell without the execution-policy prompt:
`.\.venv\Scripts\python.exe -m pytest tests\ -v` (used interchangeably
with the activated form throughout this project's own verification).

Tests run against SQLite (no Docker needed) — `tests/conftest.py`
overrides `DATABASE_URL` before any app module is imported. 14 tests:
job creation, retrieval, listing/pagination/filtering, path-traversal
rejection, worker end-to-end processing (sharp + blurry + missing
file), retries, a retry-cap-then-fail test, and two tests that directly
reproduce the exactly-once mechanics themselves (a duplicate-claim race
and the zombie-worker fencing-token scenario from DESIGN.md §2.4), not
just document them.

## Run the smoke test

Fast sanity check after `docker compose up` — one job, a few seconds,
before bothering with the full stress test:
```bash
python scripts/smoke_test.py
```

## Run the stress test

Assumes the API + 3 workers from "Run everything" above are already up:
```bash
python stress_test.py
```
Submits 100 jobs (cycling the 8 generated images — see the script's
docstring for why that's enough), polls until all are `done`/`failed`
(60s timeout), and asserts `attempts == 1` for every job plus an exact
`is_blurry`/`sharpness_score` match against known-verified values.
Prints `PASS`/`FAIL` with a summary. Verified locally: **100/100 done,
0 failed, attempts==1 for all 100, ~7 seconds.**

## Config (environment variables — all in `.env`, none hardcoded in `docker-compose.yml`)

| Variable | Purpose | Default |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Postgres credentials, used by both the `postgres` container's own first-boot init and `DATABASE_URL` | `jobs_app` / `change_me` / `jobs` |
| `DATABASE_URL` | Full connection string the app actually uses | `postgresql+psycopg2://jobs_app:change_me@postgres:5432/jobs` |
| `RABBITMQ_DEFAULT_USER` / `RABBITMQ_DEFAULT_PASS` | RabbitMQ credentials | `jobs_app` / `change_me` |
| `RABBITMQ_URL` | Full broker connection string | `amqp://jobs_app:change_me@rabbitmq:5672/` |
| `SAMPLE_IMAGES_DIR` | **Host-side** folder bind-mounted into api/worker — point this at wherever you unzipped/generated the images | `./sample_images` |
| `IMAGE_BASE_DIR` | **Container-internal** path the app resolves/validates `image_path` against (path-traversal guard) | `/app/sample_images` |
| `BLUR_THRESHOLD` | Laplacian-variance cutoff for `is_blurry` | `100.0` |
| `PROCESSING_TIMEOUT_SECONDS` | Reconciler Sweep 1 staleness threshold | `120` |
| `RECONCILER_INTERVAL_SECONDS` | How often the reconciler loop runs | `5` |
| `MAX_RETRIES` | Retry cap before permanent `failed` | `3` |
| `API_PORT` / `POSTGRES_PORT` / `RABBITMQ_PORT` / `RABBITMQ_MANAGEMENT_PORT` | Host-side port mappings | `8000` / `5432` / `5672` / `15672` |

`RABBITMQ_MANAGEMENT_PORT` (15672) is a dev-only UI — don't expose it
outside local development.

## Project structure

```
app/
  main.py              FastAPI app, /health, /ready
  worker.py             `python -m app.worker` entrypoint
  api/v1/endpoints/     route handlers
  core/                 config, database, RabbitMQ helpers
  models/                SQLAlchemy Job model
  schemas/               Pydantic request/response models
  services/               blur detection, business logic, the reconciler
  repositories/            every exactly-once-relevant SQL statement lives here
alembic/                 DB migrations
tests/                    pytest suite (SQLite, no Docker needed)
scripts/                  sample-image generator, smoke test
stress_test.py             the assignment's required stress test
sample_images/             gitignored — generate or unzip, see Setup
public/                    architecture diagrams referenced by DESIGN.md
```
