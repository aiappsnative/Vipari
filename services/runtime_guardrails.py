from __future__ import annotations

from urllib.parse import urlparse

from fastapi.responses import JSONResponse
from jwt.exceptions import InvalidKeyError

from config import AiProvider, AppEnv, Settings
from .activity_schema_migrations import ACTIVITY_MIGRATIONS, list_applied_activity_migrations
from .github_integration import generate_jwt
from .persistence import is_sqlite_locator
from .persistence import connect_sqlite
from .schema_migrations import MIGRATIONS, list_applied_migrations


def _is_https_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


def _is_localhost_host(value: str) -> bool:
    host = (urlparse(value.strip()).hostname or "").strip().lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _normalized_locator(value: str | None) -> str:
    return (value or "").strip()


def _activity_targets_primary_database(settings: Settings, *, activity_locator: str | None = None) -> bool:
    if not settings.has_activity_database_config:
        return False
    normalized_activity = _normalized_locator(activity_locator or settings.resolved_activity_db_path)
    normalized_primary = _normalized_locator(settings.resolved_db_path)
    return bool(normalized_activity and normalized_primary and normalized_activity == normalized_primary)


def _dev_auth_fallbacks_enabled(settings: Settings) -> list[str]:
    flags: list[str] = []
    if settings.local_debug_disable_login:
        flags.append("local_debug_disable_login")
    if not settings.has_owner_access_config:
        flags.append("local_owner_fallback")
    return flags


def _validate_github_app_private_key(settings: Settings) -> None:
    if not settings.has_github_app_credentials:
        return
    try:
        generate_jwt(
            settings.github_app_id,
            settings.github_private_key_path,
            settings.resolved_github_private_key,
        )
    except (InvalidKeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeError(
            "GitHub App credentials are configured, but the signing key is invalid. "
            "Set GITHUB_APP_PRIVATE_KEY to a valid PEM private key or point GITHUB_PRIVATE_KEY_PATH to a readable PEM file."
        ) from exc


def validate_migration_configuration(settings: Settings, *, resolved_db_path: str | None = None) -> None:
    errors: list[str] = []
    target_locator = resolved_db_path or settings.resolved_db_path

    if settings.is_production and is_sqlite_locator(target_locator):
        errors.append(
            "Production migrations cannot target SQLite persistence; run scripts/db_migrate.py against the production PostgreSQL DATABASE_URL."
        )

    if errors:
        raise RuntimeError(" ".join(errors))


def validate_activity_migration_configuration(settings: Settings, *, resolved_db_path: str | None = None) -> None:
    errors: list[str] = []
    target_locator = resolved_db_path or settings.resolved_activity_db_path

    if not target_locator:
        errors.append("Activity database migrations require ACTIVITY_DATABASE_URL or ACTIVITY_DB_PATH to be configured.")
    elif _activity_targets_primary_database(settings, activity_locator=target_locator):
        errors.append(
            "Activity database migrations must target a dedicated activity database; ACTIVITY_DATABASE_URL cannot match DATABASE_URL."
        )
    elif settings.is_production and is_sqlite_locator(target_locator):
        errors.append(
            "Production activity database migrations cannot target SQLite persistence; point ACTIVITY_DATABASE_URL at the Railway activity Postgres service."
        )

    if errors:
        raise RuntimeError(" ".join(errors))


def validate_runtime_configuration(settings: Settings) -> None:
    errors: list[str] = []
    app_base_is_localhost = _is_localhost_host(settings.app_base_url)

    if settings.queue_backend == "redis" and not settings.redis_url:
        errors.append("QUEUE_BACKEND=redis requires REDIS_URL.")
    if settings.queue_backend == "sqs" and (not settings.sqs_queue_url or not settings.sqs_dlq_url):
        errors.append("QUEUE_BACKEND=sqs requires both SQS_QUEUE_URL and SQS_DLQ_URL.")

    if settings.service_role == "webhook" and not settings.github_webhook_secret:
        errors.append("Webhook service requires GITHUB_WEBHOOK_SECRET.")
    if settings.service_role == "worker":
        if settings.resolved_ai_provider == AiProvider.FOUNDRY:
            if not settings.foundry_api_key:
                errors.append("Worker service requires FOUNDRY_API_KEY when AI_PROVIDER=foundry.")
            if not settings.azure_openai_endpoint:
                errors.append("Worker service requires AZURE_OPENAI_ENDPOINT when AI_PROVIDER=foundry.")
        elif not settings.openai_api_key:
            errors.append("Worker service requires OPENAI_API_KEY when AI_PROVIDER=openai.")
        if not settings.has_github_app_credentials:
            errors.append("Worker service requires GitHub App credentials.")

    if settings.has_github_app_credentials:
        try:
            _validate_github_app_private_key(settings)
        except RuntimeError as exc:
            errors.append(str(exc))

    # JWT secret length is enforced in all environments — a short secret weakens HS256
    # below the RFC 7518 §3.2 minimum of 256 bits / 32 bytes regardless of environment.
    if settings.has_internal_jwt_config and len(settings.internal_jwt_secret.encode("utf-8")) < 32:
        errors.append(
            "INTERNAL_JWT_SECRET must be at least 32 bytes for HS256 (RFC 7518 §3.2). "
            "Use a cryptographically random value, e.g.: openssl rand -hex 32"
        )

    if settings.is_internet_reachable_env and settings.service_role in {"api", "monolith"}:
        active_fallbacks = _dev_auth_fallbacks_enabled(settings)
        if active_fallbacks:
            errors.append(
                f"{settings.app_env.value.title()} forbids dev auth fallbacks: {', '.join(active_fallbacks)}."
            )

    if settings.local_debug_disable_login:
        if settings.app_env != AppEnv.LOCAL:
            errors.append("LOCAL_DEBUG_DISABLE_LOGIN is allowed only when APP_ENV=local.")
        if not app_base_is_localhost:
            errors.append("LOCAL_DEBUG_DISABLE_LOGIN requires APP_BASE_URL to resolve to localhost.")

    if settings.has_activity_database_config and settings.is_production and is_sqlite_locator(settings.resolved_activity_db_path):
        errors.append("Production activity logging must use ACTIVITY_DATABASE_URL pointing to PostgreSQL, not SQLite.")
    if _activity_targets_primary_database(settings):
        errors.append("Activity logging must use a dedicated database; ACTIVITY_DATABASE_URL cannot match DATABASE_URL.")

    if settings.is_production:
        if settings.service_role == "monolith":
            errors.append(
                "Production does not support SERVICE_ROLE=monolith; use Docker-deployed split services with SERVICE_ROLE=api, webhook, or worker."
            )
        try:
            validate_migration_configuration(settings)
        except RuntimeError as exc:
            errors.append(str(exc))
        if not _is_https_url(settings.app_base_url):
            errors.append("Production requires APP_BASE_URL to be an HTTPS URL.")
        if settings.service_role in {"api", "monolith"} and not settings.session_cookie_secure:
            errors.append("Production API/control-plane services require SESSION_COOKIE_SECURE=true.")
        if settings.github_private_key_path and not settings.github_app_private_key:
            errors.append("Production must use inline GITHUB_APP_PRIVATE_KEY; GITHUB_PRIVATE_KEY_PATH is local-dev only.")
        if settings.service_role in {"worker", "webhook"} and settings.queue_backend != "redis":
            errors.append("Production worker and webhook services must use QUEUE_BACKEND=redis.")
        if settings.service_role in {"api", "monolith"} and not settings.has_internal_jwt_config:
            errors.append("Production API service requires INTERNAL_JWT_SECRET to be configured.")

    if (
        settings.service_role in {"api", "monolith"}
        and settings.app_env in {AppEnv.LOCAL, AppEnv.TEST}
        and not app_base_is_localhost
        and not settings.has_owner_access_config
    ):
        errors.append(
            "Non-production API/control-plane services exposed on non-localhost hosts must configure OWNER_GITHUB_* access; "
            "local billing-owner fallback is localhost-only."
        )

    if errors:
        raise RuntimeError(" ".join(errors))


async def build_runtime_readiness(settings: Settings, *, queue_backend=None) -> dict[str, object]:
    checks: list[dict[str, str]] = []

    try:
        validate_runtime_configuration(settings)
        checks.append({"name": "config", "status": "ok", "detail": "Runtime configuration validated."})
    except RuntimeError as exc:
        checks.append({"name": "config", "status": "failed", "detail": str(exc)})

    try:
        with connect_sqlite(settings.resolved_db_path) as conn:
            conn.execute("SELECT 1").fetchone()
        checks.append(
            {
                "name": "persistence",
                "status": "ok",
                "detail": "SQLite connectivity verified." if settings.uses_sqlite else "PostgreSQL connectivity verified.",
            }
        )
    except Exception as exc:
        checks.append({"name": "persistence", "status": "failed", "detail": str(exc)})

    try:
        applied_versions = {item.version for item in list_applied_migrations(settings.resolved_db_path)}
        pending_versions = [version for version, _description, _handler in MIGRATIONS if version not in applied_versions]
        if pending_versions:
            checks.append(
                {
                    "name": "migrations",
                    "status": "failed",
                    "detail": f"Pending schema migrations: {', '.join(pending_versions)}.",
                }
            )
        else:
            checks.append({"name": "migrations", "status": "ok", "detail": "Schema migrations applied."})
    except Exception as exc:
        checks.append({"name": "migrations", "status": "failed", "detail": str(exc)})

    if settings.has_activity_database_config:
        try:
            with connect_sqlite(settings.resolved_activity_db_path) as conn:
                conn.execute("SELECT 1").fetchone()
            checks.append(
                {
                    "name": "activity_persistence",
                    "status": "ok",
                    "detail": "Activity database connectivity verified.",
                }
            )
        except Exception as exc:
            checks.append({"name": "activity_persistence", "status": "failed", "detail": str(exc)})

        try:
            applied_activity_versions = {item.version for item in list_applied_activity_migrations(settings.resolved_activity_db_path)}
            pending_activity_versions = [version for version, _description, _handler in ACTIVITY_MIGRATIONS if version not in applied_activity_versions]
            if pending_activity_versions:
                checks.append(
                    {
                        "name": "activity_migrations",
                        "status": "failed",
                        "detail": f"Pending activity schema migrations: {', '.join(pending_activity_versions)}.",
                    }
                )
            else:
                checks.append({"name": "activity_migrations", "status": "ok", "detail": "Activity schema migrations applied."})
        except Exception as exc:
            checks.append({"name": "activity_migrations", "status": "failed", "detail": str(exc)})

    if queue_backend is not None:
        try:
            depth = await queue_backend.depth()
            checks.append({"name": "queue", "status": "ok", "detail": f"Queue backend reachable (depth={depth})."})
        except Exception as exc:
            checks.append({"name": "queue", "status": "failed", "detail": str(exc)})

    overall_status = "ok" if all(check["status"] == "ok" for check in checks) else "failed"
    return {
        "status": overall_status,
        "app_env": settings.app_env,
        "service_role": settings.service_role,
        "checks": checks,
    }


def readiness_json_response(payload: dict[str, object]) -> JSONResponse:
    return JSONResponse(payload, status_code=200 if payload.get("status") == "ok" else 503)