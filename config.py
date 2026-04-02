from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_DB_PATH = str(Path(__file__).resolve().parent / "promptdrift.db")


class Settings(BaseSettings):
    github_app_id: str = ""
    github_private_key_path: str = ""
    github_app_private_key: str = ""
    github_webhook_secret: str = ""

    queue_backend: Literal["sqlite", "sqs"] = "sqlite"
    sqs_queue_url: str = ""
    sqs_dlq_url: str = ""

    database_url: str = f"sqlite:///{DEFAULT_DB_PATH}"
    audit_db_path: str = DEFAULT_DB_PATH

    redis_url: str = ""

    worker_concurrency: int = 4
    worker_max_retries: int = 3
    audit_worker_enabled: bool = True
    audit_max_attempts: int = 5
    audit_max_retry_window_seconds: float = 5400.0
    audit_worker_poll_seconds: float = 2.0
    llm_timeout_seconds: float = 30.0
    pr_diff_fetch_attempts: int = 3
    pr_diff_fetch_retry_seconds: float = 2.0

    openai_api_key: str = ""
    foundry_api_key: str = ""
    azure_openai_endpoint: str = ""
    ai_model: str = "gpt-4o"

    worker_metrics_port: int = 8003

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def ai_api_key(self) -> str:
        return self.foundry_api_key or self.openai_api_key

    @property
    def resolved_db_path(self) -> str:
        if self.database_url.startswith("sqlite:///"):
            sqlite_path = self.database_url.removeprefix("sqlite:///")
            if sqlite_path:
                return sqlite_path
        return self.audit_db_path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
