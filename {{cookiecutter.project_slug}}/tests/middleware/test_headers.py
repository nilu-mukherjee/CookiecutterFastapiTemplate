from collections.abc import Callable

from fastapi import FastAPI
from httpx import AsyncClient

from tests.middleware.conftest import client_for


async def test_security_headers_present_hsts_only_non_dev(
    client: AsyncClient, make_app: Callable[..., FastAPI]
) -> None:
    r = await client.get("/health/live")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "strict-transport-security" not in r.headers
    assert "content-security-policy" not in r.headers

    async with client_for(make_app(environment="prod")) as prod:
        rp = await prod.get("/health/live")
    assert "max-age=" in rp.headers["strict-transport-security"]
    assert rp.headers["x-content-type-options"] == "nosniff"


async def test_timing_header_present(client: AsyncClient) -> None:
    r = await client.get("/health/live")
    assert float(r.headers["x-process-time"]) >= 0


async def test_v1_has_deprecation_and_sunset_headers_v2_does_not(client: AsyncClient) -> None:
    v1 = await client.get("/api/v1/items")
    assert v1.headers["deprecation"] == "true"
    assert v1.headers["sunset"].endswith("GMT")
    v2 = await client.get("/api/v2/items")
    assert "deprecation" not in v2.headers
    assert "sunset" not in v2.headers
    health = await client.get("/health/live")
    assert "deprecation" not in health.headers


async def test_cors_credentials_only_with_explicit_origins(
    make_app: Callable[..., FastAPI],
) -> None:
    hdrs = {"Origin": "https://a.example", "Access-Control-Request-Method": "GET"}
    async with client_for(make_app(cors_origins='["https://a.example"]')) as c:
        r = await c.options("/health/live", headers=hdrs)
    assert r.headers["access-control-allow-origin"] == "https://a.example"
    assert r.headers["access-control-allow-credentials"] == "true"
    async with client_for(make_app(cors_origins='["*"]')) as c:
        r = await c.options("/health/live", headers=hdrs)
    assert "access-control-allow-credentials" not in r.headers


async def test_trusted_host_enforced_outside_dev(make_app: Callable[..., FastAPI]) -> None:
    app = make_app(environment="prod", allowed_hosts='["good.example"]')
    async with client_for(app) as c:
        assert (await c.get("/health/live")).status_code == 400
        ok = await c.get("/health/live", headers={"Host": "good.example"})
        assert ok.status_code == 200
