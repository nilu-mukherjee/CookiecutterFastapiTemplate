import asyncio
import logging

import pytest
from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis
from httpx import AsyncClient

from app.core.middleware import build_rate_limiter
from app.middleware.rate_limit_redis import RedisRateLimiter


def make_limiter(limit: int) -> tuple[RedisRateLimiter, FakeRedis]:
    client = FakeRedis(server=FakeServer(), decode_responses=True)
    return RedisRateLimiter(client, limit), client


async def test_redis_limiter_blocks_after_limit() -> None:
    limiter, _ = make_limiter(2)
    assert await limiter.hit("a")
    assert await limiter.hit("a")
    assert not await limiter.hit("a")
    assert await limiter.hit("b")


async def test_redis_limiter_window_resets() -> None:
    limiter, client = make_limiter(1)
    assert await limiter.hit("a")
    assert not await limiter.hit("a")
    await client.pexpire("ratelimit:a", 20)  # shorten the window instead of waiting 60 s
    await asyncio.sleep(0.05)
    assert await limiter.hit("a")


async def test_redis_limiter_retry_after_is_remaining_ttl_min_1() -> None:
    limiter, client = make_limiter(1)
    assert await limiter.retry_after("missing") == 1  # ttl -2
    await limiter.hit("a")
    assert 55 <= await limiter.retry_after("a") <= 60
    await client.set("ratelimit:noexp", 1)  # ttl -1
    assert await limiter.retry_after("noexp") == 1
    await client.pexpire("ratelimit:a", 200)
    assert await limiter.retry_after("a") == 1


async def test_redis_down_fails_open_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    server = FakeServer()
    limiter = RedisRateLimiter(FakeRedis(server=server, decode_responses=True), 1)
    server.connected = False
    with caplog.at_level(logging.WARNING, logger="app.rate_limit"):
        assert await limiter.hit("a")
        assert await limiter.hit("a")
    assert any(r.name == "app.rate_limit" for r in caplog.records)
    assert await limiter.retry_after("a") == 1


async def test_limiter_selection_uses_redis_when_enabled() -> None:
    assert isinstance(build_rate_limiter(), RedisRateLimiter)


async def test_rate_limit_end_to_end_with_redis(client: AsyncClient) -> None:
    for _ in range(120):
        assert (await client.get("/nope")).status_code == 404
    r = await client.get("/nope")
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1


async def test_ready_degraded_when_redis_down_but_200(
    client: AsyncClient, fake_redis: FakeServer
) -> None:
    fake_redis.connected = False
    r = await client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"] == "failing"
    assert body["checks"]["database"] == "ok"
