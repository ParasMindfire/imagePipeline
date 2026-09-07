from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.job import JobStatus
from app.schemas.job import JobCreate, JobCreateResponse, JobListResponse, JobResponse
from app.services import job_service
from app.services.job_service import InvalidImagePathError

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobCreateResponse, status_code=201)
def create_job(payload: JobCreate, db: Session = Depends(get_db)):
    try:
        job = job_service.create_job(db, image_path=payload.image_path, run_at=payload.run_at)
    except InvalidImagePathError as exc:
        # EDGECASE.md 1.2 — path traversal / escaping the allowed directory.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Deliberately NOT job.status here: create_job() publishes to the
    # queue and marks the row enqueued before returning, and that
    # publish's own db.commit() expires this session's cached copy of
    # `job` — so a sufficiently fast worker can claim it before this
    # line runs, and job.status would then read back "processing"
    # instead of "pending". Every job is unconditionally pending at
    # creation, by construction, regardless of how fast anything
    # downstream reacts to it — the response should say so, matching
    # the assignment's spec'd {"id", "status": "pending"} shape exactly.
    return JobCreateResponse(id=job.id, status=JobStatus.pending.value)


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = job_service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.get("", response_model=JobListResponse)
def list_jobs(
    status: JobStatus | None = Query(default=None),
    limit: int = Query(default=20, ge=1),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    status_value = status.value if status is not None else None
    items, total = job_service.list_jobs(db, status=status_value, limit=limit, offset=offset)
    # Echo back the *effective* limit (post server-side cap), not
    # whatever was requested — EDGECASE.md 1.8.
    effective_limit = min(limit, job_service.settings.MAX_PAGE_SIZE)
    return JobListResponse(items=items, total=total, limit=effective_limit, offset=offset)
