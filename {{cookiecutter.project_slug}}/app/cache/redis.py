{%- set realtime = cookiecutter.use_sse == "y" or cookiecutter.use_long_polling == "y" or cookiecutter.use_websocket == "y" -%}
from redis.asyncio import Redis

from app.core.config import get_settings

_client: Redis | None = None
{%- if realtime %}
_stream_client: Redis | None = None
{%- endif %}


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
{%- if realtime %}


def get_stream_redis() -> Redis:
    """Client for the event broker's blocking reads (``XREAD BLOCK``), built lazily.

    A blocking read legitimately waits for many seconds, so this client has no read
    timeout (get_redis()'s 0.25 s would abort every wait); TCP keepalive and periodic
    health checks detect dead connections instead.
    """
    global _stream_client
    if _stream_client is None:
        _stream_client = Redis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_connect_timeout=0.25,
            socket_keepalive=True,
            health_check_interval=30,
        )
    return _stream_client
{%- endif %}


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
{%- if realtime %}
    global _stream_client
    if _stream_client is not None:
        await _stream_client.aclose()
        _stream_client = None
{%- endif %}
