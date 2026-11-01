from httpx import AsyncClient

from app.middleware.rate_limit import InMemoryRateLimiter


async def test_rate_limit_returns_429_with_retry_after_on_121st_request(
    client: AsyncClient,
) -> None:
    for _ in range(120):
        r = await client.get("/nope", headers={"X-Request-ID": "r"})
        assert r.status_code == 404
    r = await client.get("/nope", headers={"X-Request-ID": "rl-1"})
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    body = r.json()
    assert body["code"] == "rate_limited"
    assert body["request_id"] == "rl-1"


async def test_health_exempt_from_rate_limit(client: AsyncClient) -> None:
    for _ in range(130):
        assert (await client.get("/health/live")).status_code == 200


async def test_x_forwarded_for_not_trusted(client: AsyncClient) -> None:
    for i in range(121):
        r = await client.get("/nope", headers={"X-Forwarded-For": f"10.0.0.{i}"})
    assert r.status_code == 429


async def test_in_memory_limiter_window_and_eviction() -> None:
    now = [1000.0]
    limiter = InMemoryRateLimiter(2, clock=lambda: now[0])
    assert await limiter.hit("a")
    assert await limiter.hit("a")
    assert not await limiter.hit("a")
    assert await limiter.hit("b")
    now[0] += 61
    assert await limiter.hit("a")
    assert len(limiter._windows) == 1  # expired "b" evicted opportunistically


async def test_retry_after_header_matches_remaining_window_seconds() -> None:
    from starlette.applications import Starlette

    from app.middleware.rate_limit import RateLimitMiddleware
    from tests.middleware.conftest import client_for

    now = [1000.0]
    limiter = InMemoryRateLimiter(1, clock=lambda: now[0])
    app = Starlette()
    app.add_middleware(RateLimitMiddleware, limiter=limiter)
    async with client_for(app) as c:  # type: ignore[arg-type]
        assert (await c.get("/x")).status_code == 404
        now[0] += 20
        r = await c.get("/x")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "40"


async def test_middleware_depends_only_on_protocol() -> None:
    from starlette.applications import Starlette

    from app.middleware.rate_limit import RateLimitMiddleware
    from tests.middleware.conftest import client_for

    class Stub:
        async def hit(self, key: str) -> bool:
            return False

        async def retry_after(self, key: str) -> int:
            return 7

    app = Starlette()
    app.add_middleware(RateLimitMiddleware, limiter=Stub())
    async with client_for(app) as c:  # type: ignore[arg-type]
        r = await c.get("/x")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "7"
