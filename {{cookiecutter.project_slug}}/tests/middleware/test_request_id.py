import re
from collections.abc import Callable

from fastapi import FastAPI
from httpx import AsyncClient

from tests.middleware.conftest import client_for


async def test_request_id_generated_and_echoed(client: AsyncClient) -> None:
    r = await client.get("/health/live")
    assert re.fullmatch(r"[0-9a-f]{32}", r.headers["x-request-id"])


async def test_request_id_inbound_preserved(client: AsyncClient) -> None:
    r = await client.get("/health/live", headers={"X-Request-ID": "abc-123.X_y"})
    assert r.headers["x-request-id"] == "abc-123.X_y"


async def test_unsafe_inbound_request_id_replaced(client: AsyncClient) -> None:
    for bad in [b"a b", b"x" * 65, b"evil\xe9", b"a;b"]:
        r = await client.get("/health/live", headers={"X-Request-ID": bad})
        echoed = r.headers["x-request-id"]
        assert echoed.encode("latin-1") != bad
        assert re.fullmatch(r"[0-9a-f]{32}", echoed)


async def test_error_body_contains_request_id(client: AsyncClient) -> None:
    r = await client.get(
        "/api/v1/items/00000000-0000-0000-0000-000000000000",
        headers={"X-Request-ID": "rid-v1"},
    )
    assert r.status_code == 404
    assert r.json()["request_id"] == "rid-v1"
    assert r.headers["x-request-id"] == "rid-v1"
    r2 = await client.get(
        "/api/v2/items/00000000-0000-0000-0000-000000000000",
        headers={"X-Request-ID": "rid-v2"},
    )
    assert r2.status_code == 404
    assert r2.json()["request_id"] == "rid-v2"


async def test_generated_request_id_in_error_body(client: AsyncClient) -> None:
    r = await client.get("/api/v2/items/00000000-0000-0000-0000-000000000000")
    assert r.json()["request_id"] == r.headers["x-request-id"]


async def test_unhandled_error_body_has_request_id(make_app: Callable[..., FastAPI]) -> None:
    app = make_app()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    async with client_for(app) as c:
        # ServerErrorMiddleware re-raises after responding; httpx surfaces it.
        c._transport.raise_app_exceptions = False  # type: ignore[attr-defined]
        r = await c.get("/boom", headers={"X-Request-ID": "rid-500"})
    assert r.status_code == 500
    assert r.json()["request_id"] == "rid-500"
