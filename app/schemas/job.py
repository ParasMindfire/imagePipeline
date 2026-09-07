"""Pydantic request/response models — the API's public contract."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class JobCreate(BaseModel):
    image_path: str = Field(..., min_length=1, max_length=1024)
    # Bonus (DESIGN.md §7): optional scheduled-run timestamp.
    run_at: datetime | None = None


class JobCreateResponse(BaseModel):
    id: str
    status: str


class JobResponse(BaseModel):
    id: str
    status: str
    result: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    # Not in the assignment's literal response shape — included because
    # stress_test.py has no other way to check attempts == 1.
    # See DESIGN.md "Assumptions".
    attempts: int

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int
    limit: int
    offset: int
