from redis.asyncio import Redis

from app.core.config import get_settings

_client: Redis | None = None


def get_redis() -> Redis:
    """Process-wide async Redis client, built lazily (no connection at import time)."""
    global _client
    if _client is None:
        _client = Redis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
