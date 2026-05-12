import time
import logging
from datetime import datetime, UTC

from producer.queue import get_queue_client
from worker.notifier import get_notifier
from worker.extractor import extract_metadata, ExtractionError
from worker.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    queue = get_queue_client()
    notifier = get_notifier()
    
    logger.info(f"Worker started. Polling every {settings.poll_interval_seconds} seconds.")
    
    while True:
        message = queue.receive_message()
        if message is None:
            time.sleep(settings.poll_interval_seconds)
            continue
        
        receipt_handle, payload = message
        logger.info(f"Received payload: {payload}")  # ADD THIS
        # Skip messages without job_id
        if "job_id" not in payload:
            logger.warning(f"Skipping invalid message: {payload}")
            queue.delete_message(receipt_handle)
            continue

        job_id = payload["job_id"]
        url = payload["url"]
        notify_email = payload.get("notify_email")
        result = extract_metadata(url, timeout=settings.request_timeout_seconds)
        logger.info(f"Job {job_id} completed successfully")
        logger.info(f"Extracted: title={result.get('title')}, words={result.get('word_count')}, links={result.get('link_count')}")
        logger.info(f"Processing job {job_id} for URL {url}")
        
        try:
            result = extract_metadata(url, timeout=settings.request_timeout_seconds)
            logger.info(f"Job {job_id} completed successfully")
            
            if notify_email:
                notifier.notify(
                    subject=f"Job {job_id} completed",
                    message=f"Processed {url}\nTitle: {result.get('title')}\nWord count: {result.get('word_count')}",
                )
        
        except ExtractionError as exc:
            logger.error(f"Job {job_id} failed: {exc}")
            if notify_email:
                notifier.notify(
                    subject=f"Job {job_id} failed",
                    message=f"Failed to process {url}\nError: {str(exc)}",
                )
        
        queue.delete_message(receipt_handle)


if __name__ == "__main__":
    main()