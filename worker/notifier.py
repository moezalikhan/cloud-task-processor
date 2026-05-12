import logging
from abc import ABC, abstractmethod

import boto3

logger = logging.getLogger(__name__)


class Notifier(ABC):
    @abstractmethod
    def notify(self, subject: str, message: str) -> None:
        pass


class LogNotifier(Notifier):
    def notify(self, subject: str, message: str) -> None:
        logger.info(f"NOTIFY [{subject}]: {message}")


class SNSNotifier(Notifier):
    def __init__(self, topic_arn: str, region: str):
        self.topic_arn = topic_arn
        self.client = boto3.client("sns", region_name=region)

    def notify(self, subject: str, message: str) -> None:
        self.client.publish(
            TopicArn=self.topic_arn,
            Subject=subject[:100],
            Message=message,
        )
        logger.info(f"SNS: published '{subject}'")


def get_notifier() -> Notifier:
    from worker.config import settings

    if settings.use_local_notifier:
        return LogNotifier()

    return SNSNotifier(
        topic_arn=settings.sns_topic_arn,
        region=settings.aws_region,
    )