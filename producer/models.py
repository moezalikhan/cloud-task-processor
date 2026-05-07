from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, HttpUrl


class JobCreateRequest(BaseModel):
    """Request payload for POST /jobs."""
    url: HttpUrl
    notify_email: str | None = None


class JobCreateResponse(BaseModel):
    """Response from POST /jobs."""
    job_id: UUID
    status: str = "queued"
    submitted_at: datetime


class JobStatusResponse(BaseModel):
    """Response from GET /jobs/{job_id}."""
    job_id: UUID
    status: str
    submitted_at: datetime
    completed_at: datetime | None = None
    result: dict | None = None  # Fixed: was 'results'
    error: str | None = None