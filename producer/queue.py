from collections import deque
import logging

logger = logging.getLogger(__name__)

class LocalQueue:

    def __init__(self):
        self._queue = deque()
        self._counter = 0

    def send(self, payload) -> str:
        self._counter += 1
        message_id = f"local.{self._counter}"
        self._queue.append((message_id,payload))
        logger.info(f"Queue : enqueued {message_id}")
        return message_id
    
    def recieve(self) -> tuple[str, dict] | None:
        if not self._queue:
            return None
        message_id, payload = self._queue.popleft()
        logger.info(f"Queue : recieved {message_id}")
        return message_id, payload
    
    def delete(self, message_id : str) -> None:
        logger.info(f"Queue: Deleted {message_id}")
