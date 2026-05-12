from collections import deque
from abc import ABC, abstractmethod
from typing import Any
import logging
import json
import boto3

logger = logging.getLogger(__name__)

class QueueClient(ABC):

    @abstractmethod
    def send_message(self,payload: dict[str,Any]) -> str:
        pass

    @abstractmethod
    def receive_message(self) -> tuple[str, dict[str, Any]] | None:
        pass

    @abstractmethod
    def delete_message(self, receipt_handle: str) -> None:
        pass


class LocalQueue(QueueClient):

    def __init__(self):
        self._queue = deque()
        self._counter = 0

    def send_message(self, payload) -> str:
        self._counter += 1
        message_id = f"local.{self._counter}"
        self._queue.append((message_id,payload))
        logger.info(f"Queue : enqueued {message_id}")
        return message_id
    
    def receive_message(self) -> tuple[str, dict] | None:
        if not self._queue:
            return None
        message_id, payload = self._queue.popleft()
        logger.info(f"Queue : recieved {message_id}")
        return message_id, payload
    
    def delete_message(self, receipt_handle : str) -> None:
        logger.info(f"Queue: Deleted {receipt_handle}")

class SQSQueue(QueueClient):

    def __init__(self, queue_url:str, region: str):
        self.queue_url = queue_url
        self.client = boto3.client("sqs", region_name= region)
    
    def send_message(self,payload: dict[str,Any]) -> str:
        response = self.client.send_message(
            QueueUrl = self.queue_url,
            MessageBody=json.dumps(payload),
        )
        message_id = response["MessageId"]
        logger.info(f"SQS: enqueued {message_id}")
        return message_id
    
    def receive_message(self) -> tuple[str, dict[str, Any]] | None:
        response = self.client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,
        )
        messages = response.get("Messages", [])
        if not messages:
            return None
        msg = messages[0]
        return msg["ReceiptHandle"], json.loads(msg["Body"])

    def delete_message(self, receipt_handle: str) -> None:
        self.client.delete_message(
            QueueUrl=self.queue_url,
            ReceiptHandle=receipt_handle,
        )
        logger.info(f"SQS: deleted message")


def get_queue_client() -> QueueClient:
    from producer.config import settings
    
    if settings.use_local_queue:
        return LocalQueue()
    
    return SQSQueue(
        queue_url=settings.sqs_queue_url,
        region=settings.aws_region,
    )