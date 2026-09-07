#!/usr/bin/env python
"""Standalone stress test — assignment §4.

Assumes the API and 3 worker processes are ALREADY running (e.g.
`docker compose up --build -d && docker compose up --scale worker=3 -d`).
Submits 100 jobs, polls until all are done/failed (60s timeout), and
asserts:
  - total jobs == 100
  - every result.is_blurry / result.sharpness_score is correct AND
    deterministic (checked against known values — see
    EXPECTED_RESULTS below, verified directly against
    app/services/blur_service.py during development)
  - attempts == 1 for every job (no crashes injected, so every job
    should be claimed exactly once — DESIGN.md "Exactly-once processing")

Only 8 unique images exist (scripts/generate_sample_images.py) — the
100 jobs cycle through them. That's deliberate: 100 jobs tests
*concurrency* correctness (does the queue/claim/worker-parallelism
plumbing hold up under load), which has nothing to do with how many
*unique* images exist; 8 varied images (high-frequency patterns, a
naturally smooth gradient, two blur radii) already covers *algorithm*
correctness. Reusing a known fixture set is what makes the exact-value
assertion below possible in the first place.
"""
import os
import sys
import time

import requests
from dotenv import load_dotenv

# Read .env directly rather than hardcoding a port — see
# scripts/smoke_test.py for why (a host port can shift, e.g. Windows
# reserving a TCP range). API_BASE_URL still wins if set explicitly.
load_dotenv()
API_BASE_URL = os.environ.get("API_BASE_URL") or f"http://localhost:{os.environ.get('API_PORT', '8000')}"
NUM_JOBS = 100
POLL_TIMEOUT_SECONDS = 60
POLL_INTERVAL_SECONDS = 1
READY_RETRY_SECONDS = 20

# Known-good values — verified directly against blur_service.py during
# development (see the conversation history / tests/services/test_worker_e2e.py
# for the same constants). If these ever drift, either the generator or
# opencv-python-headless changed — exactly the determinism risk DESIGN.md
# calls out.
EXPECTED_RESULTS = {
    "checkerboard_sharp.png": {"is_blurry": False, "sharpness_score": 13738.2246},
    "checkerboard_blurry.png": {"is_blurry": True, "sharpness_score": 2.7206},
    "checkerboard_slightly_blurry.png": {"is_blurry": True, "sharpness_score": 31.9404},
    "lines_sharp.png": {"is_blurry": False, "sharpness_score": 39489.564},
    "lines_blurry.png": {"is_blurry": True, "sharpness_score": 0.8794},
    "noise_sharp.png": {"is_blurry": False, "sharpness_score": 109232.6093},
    "noise_blurry.png": {"is_blurry": True, "sharpness_score": 1.1741},
    "gradient_smooth.png": {"is_blurry": True, "sharpness_score": 0.8016},
}
IMAGE_CYCLE = list(EXPECTED_RESULTS.keys())


def wait_until_ready() -> None:
    deadline = time.monotonic() + READY_RETRY_SECONDS
    while time.monotonic() < deadline:
        try:
            resp = requests.get(f"{API_BASE_URL}/ready", timeout=3)
            if resp.status_code == 200:
                print("API is ready.")
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    print(f"FAIL: API not ready after {READY_RETRY_SECONDS}s — is `docker compose up` running?")
    sys.exit(1)


def submit_jobs() -> dict[str, str]:
    """Returns {job_id: image_filename_used}."""
    submitted: dict[str, str] = {}
    for i in range(NUM_JOBS):
        image_name = IMAGE_CYCLE[i % len(IMAGE_CYCLE)]
        resp = requests.post(f"{API_BASE_URL}/jobs", json={"image_path": image_name}, timeout=10)
        if resp.status_code != 201:
            print(f"FAIL: job submission {i} returned {resp.status_code}: {resp.text}")
            sys.exit(1)
        job_id = resp.json()["id"]
        submitted[job_id] = image_name
    print(f"Submitted {len(submitted)} jobs.")
    return submitted


def poll_until_terminal(job_ids: list[str]) -> dict[str, dict]:
    """Returns {job_id: full job body} for every job, terminal or not
    (a job still pending/processing at timeout is included as-is, and
    the caller's assertions will fail loudly on it)."""
    results: dict[str, dict] = {}
    remaining = set(job_ids)
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS

    while remaining and time.monotonic() < deadline:
        still_remaining = set()
        for job_id in remaining:
            resp = requests.get(f"{API_BASE_URL}/jobs/{job_id}", timeout=10)
            body = resp.json()
            results[job_id] = body
            if body["status"] in ("pending", "processing"):
                still_remaining.add(job_id)
        remaining = still_remaining
        if remaining:
            time.sleep(POLL_INTERVAL_SECONDS)

    for job_id in remaining:  # anything left is a timeout — record final state
        resp = requests.get(f"{API_BASE_URL}/jobs/{job_id}", timeout=10)
        results[job_id] = resp.json()

    return results


def main() -> None:
    wait_until_ready()

    start = time.monotonic()
    submitted = submit_jobs()
    results = poll_until_terminal(list(submitted.keys()))
    elapsed = time.monotonic() - start

    failures: list[str] = []

    if len(results) != NUM_JOBS:
        failures.append(f"expected {NUM_JOBS} jobs, got {len(results)} results")

    not_terminal = [jid for jid, body in results.items() if body["status"] not in ("done", "failed")]
    if not_terminal:
        failures.append(f"{len(not_terminal)} job(s) did not reach done/failed within {POLL_TIMEOUT_SECONDS}s: {not_terminal[:5]}...")

    wrong_attempts = [jid for jid, body in results.items() if body.get("attempts") != 1]
    if wrong_attempts:
        failures.append(f"{len(wrong_attempts)} job(s) had attempts != 1: {[(j, results[j].get('attempts')) for j in wrong_attempts[:5]]}...")
    
    SCORE_TOLERANCE = 1e-6
    wrong_results = []
    for job_id, image_name in submitted.items():
        body = results.get(job_id, {})
        if body.get("status") != "done":
            continue
        expected = EXPECTED_RESULTS[image_name]
        actual = body.get("result") or {}
        score_matches = abs(actual.get("sharpness_score", float("nan")) - expected["sharpness_score"]) < SCORE_TOLERANCE
        if actual.get("is_blurry") != expected["is_blurry"] or not score_matches:
            wrong_results.append((job_id, image_name, expected, actual))
    if wrong_results:
        failures.append(f"{len(wrong_results)} job(s) had incorrect/non-deterministic results: {wrong_results[:3]}...")

    print(f"\nCompleted in {elapsed:.1f}s.")
    print(f"  total jobs:        {len(results)}")
    print(f"  done:              {sum(1 for b in results.values() if b['status'] == 'done')}")
    print(f"  failed:            {sum(1 for b in results.values() if b['status'] == 'failed')}")
    print(f"  attempts == 1:     {sum(1 for b in results.values() if b.get('attempts') == 1)}/{len(results)}")

    if failures:
        print("\nFAIL")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)

    print("\nPASS")


if __name__ == "__main__":
    main()
