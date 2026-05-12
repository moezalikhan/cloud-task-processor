from pydantic_settings import BaseSettings


class WorkerSettings(BaseSettings):
    log_level: str = "INFO"
    aws_region: str = "eu-north-1"
    sqs_queue_url: str = ""
    sns_topic_arn: str = ""
    use_local_queue: bool = True
    use_local_notifier: bool = True
    poll_interval_seconds: int = 5
    request_timeout_seconds: int = 10

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = WorkerSettings()