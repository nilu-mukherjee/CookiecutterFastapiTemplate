{%- set bearer = cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set cookie = cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set session = cookiecutter.use_auth_session == "y" -%}
{%- set api_key = cookiecutter.use_auth_api_key == "y" -%}
{%- set basic = cookiecutter.use_auth_basic == "y" -%}
{%- set methods = [] -%}
{%- if bearer %}{% set _ = methods.append("bearer") %}{% endif -%}
{%- if api_key %}{% set _ = methods.append("api_key") %}{% endif -%}
{%- if session %}{% set _ = methods.append("session") %}{% endif -%}
{%- if cookie %}{% set _ = methods.append("jwt_cookie") %}{% endif -%}
{%- if basic %}{% set _ = methods.append("basic") %}{% endif -%}
"""Clients for every auth method enabled in this build, obtained through the real paths."""
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.models import User
from tests.conftest import (
    PASSWORD,
{%- if api_key %}
    MakeApiKey,
{%- endif %}
{%- if cookie %}
    MakeCookieClient,
{%- endif %}
{%- if session %}
    MakeSessionClient,
{%- endif %}
{%- if basic %}
    basic_header,
{%- endif %}
)

# The enabled methods, in precedence order. Isolation tests run once per method.
METHODS = [{% for m in methods %}"{{ m }}"{% if not loop.last %}, {% endif %}{% endfor %}]

MethodClient = Callable[..., Awaitable[AsyncClient]]


@pytest_asyncio.fixture
async def method_client(
    application: FastAPI,
{%- if api_key %}
    make_api_key: MakeApiKey,
{%- endif %}
{%- if cookie %}
    make_cookie_client: MakeCookieClient,
{%- endif %}
{%- if session %}
    make_session_client: MakeSessionClient,
{%- endif %}
) -> AsyncIterator[MethodClient]:
    """A client authenticated as the user (``password``) with the named method.

    Each credential comes from the method's real path: the login endpoint (Bearer,
    cookie, session), the API key service, or a Basic header checked on every request.
    """
    clients: list[AsyncClient] = []

    def new_client(headers: dict[str, str] | None = None) -> AsyncClient:
        c = AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test", headers=headers
        )
        clients.append(c)
        return c

    async def _make(method: str, user: User, password: str = PASSWORD) -> AsyncClient:
{%- if bearer %}
        if method == "bearer":
            c = new_client()
            r = await c.post(
                "/api/v1/auth/login",
                json={"email": user.email, "password": password{% if cookie or session %}, "delivery": "bearer"{% endif %}},
            )
            assert r.status_code == 200, r.text
            c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
            return c
{%- endif %}
{%- if api_key %}
        if method == "api_key":
            return new_client({"X-API-Key": await make_api_key(user)})
{%- endif %}
{%- if session %}
        if method == "session":
            return await make_session_client(user.email, password)
{%- endif %}
{%- if cookie %}
        if method == "jwt_cookie":
            return await make_cookie_client(user.email, password)
{%- endif %}
{%- if basic %}
        if method == "basic":
            return new_client(basic_header(user.email, password))
{%- endif %}
        raise AssertionError(f"unknown method {method}")

    yield _make
    for c in clients:
        await c.aclose()

