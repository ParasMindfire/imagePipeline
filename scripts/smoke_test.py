#!/usr/bin/env python
"""Smoke test — a fast sanity check after `docker compose up`, distinct
from stress_test.py's full 100-job run. Checks /health, /ready, and one
job end to end, in a few seconds. Meant to answer "did the deployment
even come up correctly" before bothering to run the real stress test.

Run: python scripts/smoke_test.py
"""
import os
import sys
import time

import requests
from dotenv import load_dotenv

# Read .env directly rather than hardcoding a port, since the host port
# can shift (e.g. Windows/Hyper-V reserving a TCP range that includes
# 8000 after a Docker Desktop restart) — API_PORT in .env is the one
# source of truth for what's actually published, same file
# docker-compose.yml itself reads. An explicit API_BASE_URL env var
# still wins if set, for a one-off override without touching .env.
load_dotenv()
API_BASE_URL = os.environ.get("API_BASE_URL") or f"http://localhost:{os.environ.get('API_PORT', '8000')}"
TIMEOUT_SECONDS = 20


def check(label: str, condition: bool, detail: str = "") -> bool:
    status = "OK  " if condition else "FAIL"
    print(f"  [{status}] {label}{f' — {detail}' if detail and not condition else ''}")
    return condition


def main() -> None:
    print("Smoke test")
    ok = True

    try:
        resp = requests.get(f"{API_BASE_URL}/health", timeout=5)
        ok &= check("GET /health returns 200", resp.status_code == 200, f"got {resp.status_code}")
    except requests.RequestException as exc:
        ok &= check("GET /health reachable", False, str(exc))
        print("\nFAIL — API not reachable at all, stopping here.")
        sys.exit(1)

    resp = requests.get(f"{API_BASE_URL}/ready", timeout=5)
    ready_body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    ok &= check("GET /ready reports database reachable", ready_body.get("checks", {}).get("database") is True)
    ok &= check("GET /ready reports rabbitmq reachable", ready_body.get("checks", {}).get("rabbitmq") is True)

    resp = requests.post(f"{API_BASE_URL}/jobs", json={"image_path": "checkerboard_sharp.png"}, timeout=10)
    ok &= check("POST /jobs returns 201", resp.status_code == 201, f"got {resp.status_code}: {resp.text}")
    if resp.status_code != 201:
        print("\nFAIL")
        sys.exit(1)
    job_id = resp.json()["id"]
    print(f"       submitted job {job_id}")

    deadline = time.monotonic() + TIMEOUT_SECONDS
    body = {}
    while time.monotonic() < deadline:
        resp = requests.get(f"{API_BASE_URL}/jobs/{job_id}", timeout=5)
        body = resp.json()
        if body.get("status") in ("done", "failed"):
            break
        time.sleep(1)

    ok &= check(f"job reached a terminal state within {TIMEOUT_SECONDS}s", body.get("status") in ("done", "failed"), f"status={body.get('status')}")
    ok &= check("job status is 'done' (not 'failed')", body.get("status") == "done", str(body.get("result")))
    ok &= check("attempts == 1", body.get("attempts") == 1, f"attempts={body.get('attempts')}")
    ok &= check(
        "result matches known value for checkerboard_sharp.png",
        body.get("result") == {"is_blurry": False, "sharpness_score": 13738.2246},
        str(body.get("result")),
    )

    print("\nPASS" if ok else "\nFAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
