from __future__ import annotations

from fastapi import APIRouter, HTTPException

from config import Settings
from services.runtime_guardrails import build_runtime_readiness, public_operational_routes_enabled, readiness_json_response


def require_health_routes_enabled(settings: Settings) -> None:
    if not public_operational_routes_enabled(settings):
        raise HTTPException(status_code=404, detail="Not Found")


def create_health_router(settings: Settings) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health")
    async def health_live():
        require_health_routes_enabled(settings)
        return {"status": "ok", "service_role": settings.service_role}

    @router.get("/health/ready")
    async def health_ready():
        require_health_routes_enabled(settings)
        return readiness_json_response(await build_runtime_readiness(settings))

    return router