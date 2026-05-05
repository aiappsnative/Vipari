from __future__ import annotations

from fastapi import APIRouter

from config import Settings
from services.runtime_guardrails import build_runtime_readiness, readiness_json_response


def create_health_router(settings: Settings) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health")
    async def health_live():
        return {"status": "ok", "service_role": settings.service_role}

    @router.get("/health/ready")
    async def health_ready():
        return readiness_json_response(await build_runtime_readiness(settings))

    return router