from __future__ import annotations

import time
from typing import Any

from redis import asyncio as redis_asyncio

from config import get_settings


DEFAULT_LOCAL_CACHE_TTL_MINUTES = 55
# Cache slightly under GitHub's one-hour installation-token expiry to avoid serving stale credentials.
_LOCAL_CACHE_TTL_SECONDS = DEFAULT_LOCAL_CACHE_TTL_MINUTES * 60
_local_cache: dict[int, tuple[str, float]] = {}
_redis_client: Any | None = None


def _redis_key(installation_id: int) -> str:
    return f"driftguard:installation-token:{installation_id}"


async def _get_redis_client() -> Any | None:
    global _redis_client
    settings = get_settings()
    if not settings.redis_url:
        return None
    if _redis_client is None:
        _redis_client = redis_asyncio.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    return _redis_client


async def get_installation_token(installation_id: int) -> str | None:
    redis_client = await _get_redis_client()
    if redis_client is not None:
        return await redis_client.get(_redis_key(installation_id))

    cached = _local_cache.get(installation_id)
    if cached is None:
        return None
    token, expires_at = cached
    if expires_at <= time.time():
        _local_cache.pop(installation_id, None)
        return None
    return token


async def set_installation_token(installation_id: int, token: str, expires_in: int) -> None:
    ttl_seconds = max(1, min(expires_in, _LOCAL_CACHE_TTL_SECONDS))
    redis_client = await _get_redis_client()
    if redis_client is not None:
        await redis_client.set(_redis_key(installation_id), token, ex=ttl_seconds)
        return

    _local_cache[installation_id] = (token, time.time() + ttl_seconds)


def clear_local_token_cache() -> None:
    _local_cache.clear()
