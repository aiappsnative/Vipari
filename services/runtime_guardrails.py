from __future__ import annotations

from urllib.parse import urlparse

from fastapi.responses import JSONResponse
from jwt.exceptions import InvalidKeyError

from config import Settings
from .github_integration import generate_jwt
from .persistence import connect_sqlite


def _is_https_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


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


def validate_runtime_configuration(settings: Settings) -> None:
    errors: list[str] = []

    if settings.queue_backend == "redis" and not settings.redis_url:
        errors.append("QUEUE_BACKEND=redis requires REDIS_URL.")
    if settings.queue_backend == "sqs" and (not settings.sqs_queue_url or not settings.sqs_dlq_url):
        errors.append("QUEUE_BACKEND=sqs requires both SQS_QUEUE_URL and SQS_DLQ_URL.")

    if settings.service_role == "webhook" and not settings.github_webhook_secret:
        errors.append("Webhook service requires GITHUB_WEBHOOK_SECRET.")
    if settings.service_role == "worker":
        if not settings.ai_api_key:
            errors.append("Worker service requires OPENAI_API_KEY or FOUNDRY_API_KEY.")
        if not settings.has_github_app_credentials:
            errors.append("Worker service requires GitHub App credentials.")

    if settings.has_github_app_credentials:
        try:
            _validate_github_app_private_key(settings)
        except RuntimeError as exc:
            errors.append(str(exc))

    if settings.is_production:
        if not _is_https_url(settings.app_base_url):
            errors.append("Production requires APP_BASE_URL to be an HTTPS URL.")
        if settings.service_role in {"api", "monolith"} and not settings.session_cookie_secure:
            errors.append("Production API/control-plane services require SESSION_COOKIE_SECURE=true.")
        if settings.github_private_key_path and not settings.github_app_private_key:
            errors.append("Production must use inline GITHUB_APP_PRIVATE_KEY; GITHUB_PRIVATE_KEY_PATH is local-dev only.")
        if settings.uses_sqlite:
            errors.append("Production cannot use SQLite persistence; PostgreSQL-backed persistence is still required before launch.")
        if settings.service_role in {"worker", "webhook"} and settings.queue_backend != "redis":
            errors.append("Production worker and webhook services must use QUEUE_BACKEND=redis.")

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