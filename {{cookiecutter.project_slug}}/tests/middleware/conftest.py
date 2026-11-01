{%- set cookie = cookiecutter.use_auth == "y" and (cookiecutter.use_auth_jwt_cookie == "y" or cookiecutter.use_auth_session == "y") -%}
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
def make_app(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FastAPI]:
    """Build an app after applying env overrides (settings are cached)."""

    def _make(**env: str) -> FastAPI:
{%- if cookie %}
        monkeypatch.setenv("COOKIE_SECURE", "true")  # required outside dev
{%- endif %}
        for key, value in env.items():
            monkeypatch.setenv(key.upper(), value)
        get_settings.cache_clear()
        return create_app()

    return _make


@pytest.fixture(autouse=True)
def _reset_settings() -> object:
    yield
    get_settings.cache_clear()


def client_for(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
