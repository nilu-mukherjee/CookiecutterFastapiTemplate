import logging
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.middleware.access_log import AccessLogMiddleware
from app.middleware.deprecation import DeprecationMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.timing import TimingMiddleware
from tests.middleware.conftest import client_for


def _classes(app: FastAPI) -> list[type]:
    return [m.cls for m in app.user_middleware]


def test_middleware_order_is_request_id_outermost(make_app: Callable[..., FastAPI]) -> None:
    assert _classes(make_app()) == [
        RequestIdMiddleware,
        AccessLogMiddleware,
        TimingMiddleware,
        SecurityHeadersMiddleware,
        CORSMiddleware,
        GZipMiddleware,
        RateLimitMiddleware,
        DeprecationMiddleware,
    ]
    assert _classes(make_app(environment="prod")) == [
        RequestIdMiddleware,
        AccessLogMiddleware,
        TimingMiddleware,
        SecurityHeadersMiddleware,
        CORSMiddleware,
        GZipMiddleware,
        TrustedHostMiddleware,
        RateLimitMiddleware,
        DeprecationMiddleware,
    ]


async def test_access_log_line_has_method_path_status_duration_request_id(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.access"):
        await client.get("/health/live", headers={"X-Request-ID": "log-1"})
    recs = [r for r in caplog.records if r.name == "app.access"]
    assert len(recs) == 1
    rec = recs[0]
    assert rec.method == "GET"  # type: ignore[attr-defined]
    assert rec.path == "/health/live"  # type: ignore[attr-defined]
    assert rec.status == 200  # type: ignore[attr-defined]
    assert rec.duration_ms >= 0  # type: ignore[attr-defined]
    assert rec.request_id == "log-1"  # type: ignore[attr-defined]


async def test_access_log_records_500_when_handler_raises(
    make_app: Callable[..., FastAPI], caplog: pytest.LogCaptureFixture
) -> None:
    app = make_app()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    with caplog.at_level(logging.INFO, logger="app.access"):
        async with client_for(app) as c:
            c._transport.raise_app_exceptions = False  # type: ignore[attr-defined]
            await c.get("/boom")
    rec = next(r for r in caplog.records if r.name == "app.access")
    assert rec.status == 500  # type: ignore[attr-defined]


def test_create_app_keeps_foreign_handlers_and_caplog_works(
    make_app: Callable[..., FastAPI], caplog: pytest.LogCaptureFixture
) -> None:
    make_app()
    make_app()
    root = logging.getLogger()
    assert caplog.handler in root.handlers
    ours = [h for h in root.handlers if getattr(h, "_app_handler", False)]
    assert len(ours) == 1
    with caplog.at_level(logging.INFO):
        logging.getLogger("x").info("hello")
    assert "hello" in caplog.text


def test_json_formatter_includes_request_id() -> None:
    from app.core.logging import JsonFormatter
    from app.middleware.request_id import request_id_var

    token = request_id_var.set("fmt-1")
    try:
        rec = logging.LogRecord("n", logging.INFO, "f", 1, "msg", None, None)
        out = JsonFormatter().format(rec)
    finally:
        request_id_var.reset(token)
    assert '"request_id": "fmt-1"' in out
