"""Covers the assignment's required minimum: create job, get job.
(The third required case — worker processes a job end-to-end — is in
tests/services/test_worker_e2e.py.)"""


def test_create_job_returns_pending(client, sample_image_path):
    resp = client.post("/jobs", json={"image_path": sample_image_path})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert isinstance(body["id"], str) and body["id"]


def test_create_job_rejects_path_traversal(client):
    # EDGECASE.md 1.2 — must not be able to escape IMAGE_BASE_DIR.
    resp = client.post("/jobs", json={"image_path": "../../etc/passwd"})
    assert resp.status_code == 400


def test_get_job_returns_full_shape(client, sample_image_path):
    created = client.post("/jobs", json={"image_path": sample_image_path}).json()

    resp = client.get(f"/jobs/{created['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == created["id"]
    assert body["status"] == "pending"
    assert body["result"] is None
    assert body["attempts"] == 0  # not claimed yet — DESIGN.md "Assumptions"
    assert "created_at" in body and "updated_at" in body


def test_get_job_404_for_unknown_id(client):
    resp = client.get("/jobs/does-not-exist")
    assert resp.status_code == 404


def test_list_jobs_filters_by_status_and_paginates(client, sample_image_path):
    for _ in range(3):
        client.post("/jobs", json={"image_path": sample_image_path})

    resp = client.get("/jobs?status=pending&limit=2&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert all(item["status"] == "pending" for item in body["items"])
    assert body["limit"] == 2
    assert body["offset"] == 0


def test_list_jobs_limit_is_capped_server_side(client, sample_image_path):
    # EDGECASE.md 1.8 — a client can't demand an unbounded page.
    resp = client.get("/jobs?limit=999999")
    assert resp.status_code == 200
    assert resp.json()["limit"] <= 100
