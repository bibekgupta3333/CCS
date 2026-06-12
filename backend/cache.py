import json
import logging
import os
from typing import Any, Optional

import redis.asyncio as redis

logger = logging.getLogger(__name__)

CACHE_TTL = 3600

_redis_client: Optional[redis.Redis] = None
_circuit_open = False


def _get_client() -> Optional[redis.Redis]:
    global _redis_client
    if _redis_client is None:
        host = os.getenv("REDIS_HOST", "ccs-redis-master")
        port = int(os.getenv("REDIS_PORT", "6379"))
        _redis_client = redis.Redis(
            host=host,
            port=port,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _redis_client


async def get_cached(key: str) -> Optional[Any]:
    global _circuit_open  # noqa: PLW0603
    if _circuit_open:
        return None
    try:
        client = _get_client()
        if client is None:
            return None
        val = await client.get(key)
        if val is not None:
            logger.info(f"Cache HIT: {key}")
            return json.loads(val)
        logger.info(f"Cache MISS: {key}")
        return None
    except Exception as e:
        logger.warning(f"Redis unavailable, circuit break: {e}")
        _circuit_open = True
        return None


async def set_cached(key: str, value: Any, ttl: int = CACHE_TTL) -> bool:
    if _circuit_open:
        return False
    try:
        client = _get_client()
        if client is None:
            return False
        await client.set(key, json.dumps(value), ex=ttl)
        return True
    except Exception as e:
        logger.warning(f"Redis write failed: {e}")
        return False


async def invalidate(key: str) -> None:
    try:
        client = _get_client()
        if client:
            await client.delete(key)
    except Exception:
        pass


def reset_circuit() -> None:
    global _circuit_open
    _circuit_open = False