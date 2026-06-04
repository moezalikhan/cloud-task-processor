import logging
from datetime import datetime, UTC
from fastapi import FastAPI
from fastapi import HTTPException
from uuid import UUID, uuid4
from producer.models import JobCreateRequest, JobCreateResponse, JobStatusResponse
from producer.queue import get_queue_client
from producer.config import settings
from producer.db import SessionLocal, Job, init_db

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)
init_db()

queue = get_queue_client()
app = FastAPI(title=settings.app_name, version="0.1.0")


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

    session = SessionLocal()
    try:
        job = Job(
            job_id=str(job_id),
            url=str(request.url),
            notify_email=request.notify_email,
            status="queued",
            submitted_at=submitted_at,
        )
        session.add(job)
        session.commit()
    finally:
        session.close()

    queue.send_message({
        "job_id": str(job_id),
        "url": str(request.url),
        "notify_email": request.notify_email,
        "status": "queued",
        "submitted_at": submitted_at.isoformat(),
    })
    logger.info("Job %s queued for URL %s", job_id, request.url)

    return JobCreateResponse(
        job_id=job_id,
        status="queued",
        submitted_at=submitted_at,
    )


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: UUID):
    """Look up a job by ID."""
    session = SessionLocal()
    try:
        job = session.get(Job, str(job_id))
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        return JobStatusResponse(
            job_id=job_id,
            status=job.status,
            submitted_at=job.submitted_at,
            completed_at=job.completed_at,
            result=job.result,
            error=job.error,
        )
    finally:
        session.close()