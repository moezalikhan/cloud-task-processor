import logging
import threading
from datetime import datetime, UTC
from fastapi import FastAPI
from fastapi import HTTPException
from uuid import UUID, uuid4
from producer.models import JobCreateRequest, JobCreateResponse, JobStatusResponse
from producer.queue import LocalQueue
from worker.worker import run_worker
from producer.config import settings

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__ )

queue = LocalQueue()

app = FastAPI(title=settings.app_name, version="0.1.0")

#  Shared Dictionary For Data
jobs :  dict[UUID , dict] = {}
queue : LocalQueue()
stop_event = threading.Event()


# Endpoints for the different operations
@app.on_event("startup")
def startup_event():
    logger.info("Starting worker Thread.")
    worker_thread = threading.Thread(
        target= run_worker,
        args= (queue,jobs,stop_event),
        daemon= True
    )
    worker_thread.start()
    logger.info("Thread Started")

@app.on_event("shutdown")
def shutdown_event():
    logger.info("Shutting Down Thread")
    stop_event.set()
    logger.info("Shutdown")

@app.get("/")
def root():
    return{"status": "ok", "service": settings.app_name}
@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "healthy", "queue": "local"}


@app.post("/jobs", response_model=JobCreateResponse, status_code=201)
def create_job(request: JobCreateRequest):
    """Submit a URL for processing."""
    job_id = uuid4()
    submitted_at = datetime.now(UTC)

    # Build job record
    job_record = {
        "job_id": str(job_id),
        "url": str(request.url),
        "notify_email": request.notify_email,
        "status": "queued",
        "submitted_at": submitted_at.isoformat(),
        "completed_at": None,
        "result": None,
        "error": None,
    }

    # Store in shared dict
    jobs[job_id] = job_record.copy()

    # Push to queue
    queue.send(job_record)
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