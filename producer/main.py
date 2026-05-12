import logging
from datetime import datetime, UTC
from fastapi import FastAPI
from fastapi import HTTPException
from uuid import UUID, uuid4
from producer.models import JobCreateRequest, JobCreateResponse, JobStatusResponse
from producer.queue import get_queue_client
from producer.config import settings

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

queue = get_queue_client()
app = FastAPI(title=settings.app_name, version="0.1.0")

# Shared dict for job status
jobs: dict[UUID, dict] = {}


@app.get("/")
def root():
    return {"status": "ok", "service": settings.app_name}


@app.get("/health")
def health():
    queue_type = "local" if settings.use_local_queue else "sqs"
    return {"status": "healthy", "queue": queue_type}


@app.post("/jobs", response_model=JobCreateResponse, status_code=201)
def create_job(request: JobCreateRequest):
    """Submit a URL for processing."""
    job_id = uuid4()
    submitted_at = datetime.now(UTC)

    job_record = {
        "job_id": str(job_id),
        "url": str(request.url),
        "notify_email": request.notify_email,
        "status": "queued",
        "submitted_at": submitted_at.isoformat(),
    }

    jobs[job_id] = {
        **job_record,
        "completed_at": None,
        "result": None,
        "error": None,
    }

    queue.send_message(job_record)
    logger.info("Job %s queued for URL %s", job_id, request.url)

    return JobCreateResponse(
        job_id=job_id,
        status="queued",
        submitted_at=submitted_at,
    )


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: UUID):
    """Look up a job by ID."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(
        job_id=job_id,
        status=job["status"],
        submitted_at=datetime.fromisoformat(job["submitted_at"]),
        completed_at=(
            datetime.fromisoformat(job["completed_at"])
            if job.get("completed_at")
            else None
        ),
        result=job.get("result"),
        error=job.get("error"),
    )