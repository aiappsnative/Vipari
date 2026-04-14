from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_DB_PATH = str(Path(__file__).resolve().parent / "promptdrift.db")
PROJECT_ENV_PATH = Path(__file__).resolve().parent / ".env"


class Settings(BaseSettings):
    app_base_url: str = "http://127.0.0.1:8000"
    session_cookie_name: str = "promptdrift_session"
    session_cookie_secure: bool = False
    session_ttl_seconds: int = 604800
    app_encryption_key: str = ""

    github_app_id: str = ""
    github_private_key_path: str = ""
    github_app_private_key: str = ""
    github_webhook_secret: str = ""
    github_oauth_client_id: str = ""
    github_oauth_client_secret: str = ""
    github_oauth_callback_url: str = ""

    queue_backend: Literal["sqlite", "sqs"] = "sqlite"
    sqs_queue_url: str = ""
    sqs_dlq_url: str = ""

    database_url: str = f"sqlite:///{DEFAULT_DB_PATH}"
    audit_db_path: str = DEFAULT_DB_PATH

    redis_url: str = ""
    api_admin_token: str = ""

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_portal_configuration_id: str = ""
    stripe_price_starter: str = ""
    stripe_price_team: str = ""
    stripe_price_enterprise: str = ""
    stripe_price_business: str = ""
    billing_handoff_secret: str = ""
    billing_handoff_ttl_seconds: int = 86400
    base44_checkout_url: str = ""
    owner_github_login: str = ""
    owner_github_user_id: str = ""
    owner_email: str = ""
    admin_github_logins: str = ""
    admin_github_user_ids: str = ""
    admin_emails: str = ""

    worker_concurrency: int = 4
    worker_max_retries: int = 3
    enable_metrics: bool = False
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

    model_config = SettingsConfigDict(env_file=str(PROJECT_ENV_PATH), extra="ignore")

    @property
    def ai_api_key(self) -> str:
        return self.foundry_api_key or self.openai_api_key

    @property
    def resolved_github_private_key(self) -> str:
        if not self.github_app_private_key:
            return ""
        return self.github_app_private_key.replace("\\n", "\n")

    @property
    def has_github_app_credentials(self) -> bool:
        return bool(self.github_app_id and (self.github_private_key_path or self.resolved_github_private_key))

    @property
    def has_github_oauth_credentials(self) -> bool:
        return bool(self.github_oauth_client_id and self.github_oauth_client_secret)

    @property
    def has_stripe_billing_config(self) -> bool:
        return bool(self.stripe_secret_key and self.stripe_webhook_secret)

    @property
    def has_encryption_key(self) -> bool:
        return bool(self.app_encryption_key)

    @property
    def admin_github_login_set(self) -> set[str]:
        return {value.strip().lower() for value in self.admin_github_logins.split(",") if value.strip()}

    @property
    def admin_github_user_id_set(self) -> set[str]:
        return {value.strip() for value in self.admin_github_user_ids.split(",") if value.strip()}

    @property
    def admin_email_set(self) -> set[str]:
        return {value.strip().lower() for value in self.admin_emails.split(",") if value.strip()}

    @property
    def has_admin_access_config(self) -> bool:
        return bool(self.admin_github_login_set or self.admin_github_user_id_set or self.admin_email_set)

    @property
    def normalized_owner_github_login(self) -> str:
        return self.owner_github_login.strip().lower()

    @property
    def normalized_owner_email(self) -> str:
        return self.owner_email.strip().lower()

    @property
    def has_owner_access_config(self) -> bool:
        return bool(self.owner_github_user_id.strip() or self.normalized_owner_github_login or self.normalized_owner_email)

    @property
    def resolved_db_path(self) -> str:
        if "audit_db_path" in self.model_fields_set and self.audit_db_path:
            return self.audit_db_path
        if self.database_url.startswith("sqlite:///"):
            sqlite_path = self.database_url.removeprefix("sqlite:///")
            if sqlite_path:
                return sqlite_path
        if self.database_url and not self.database_url.startswith("sqlite:///"):
            return self.audit_db_path
        return self.audit_db_path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
