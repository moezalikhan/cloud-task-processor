from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "cloud-task-processor"
    log_level: str = "INFO"


settings = Settings()