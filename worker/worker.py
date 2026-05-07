import time
import logging
from datetime import datetime, UTC
from uuid import UUID

from producer.queue import LocalQueue
from worker.extractor import extract_metadata, ExtractionError

logger = logging.getLogger(__name__)

def run_worker(queue : LocalQueue, jobs : dict, stop_event)-> None:
    logger.info("Worker Started Polling every 5 Seconds")
    while not stop_event.is_set():
        # poll queue
        message = queue.recieve()
        if message is None:
            time.sleep(5)
            continue
        message_id, payload = message
        job_id = UUID(payload["job_id"])
        url = payload["url"]
        logger.info(f"Processing job {job_id} for URL {url}")
        jobs[job_id]["status"] = "processing"

        try:
            # Extract metadata
            result = extract_metadata(url)
            
            # Update job with success
            jobs[job_id]["status"] = "completed"
            jobs[job_id]["completed_at"] = datetime.now(UTC).isoformat()
            jobs[job_id]["result"] = result
            
            logger.info(f"Job {job_id} completed successfully")

        except ExtractionError as exc:
            # Update job with failure
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["completed_at"] = datetime.now(UTC).isoformat()
            jobs[job_id]["error"] = str(exc)
            
            logger.error(f"Job {job_id} failed: {exc}")

        # Acknowledge the message
        queue.delete(message_id)

    logger.info("Worker stopped.")