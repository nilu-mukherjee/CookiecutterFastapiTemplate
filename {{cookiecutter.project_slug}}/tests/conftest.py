{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set bearer = auth and cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set cookie = auth and cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set session = auth and cookiecutter.use_auth_session == "y" -%}
{%- set api_key = auth and cookiecutter.use_auth_api_key == "y" -%}
{%- set basic = auth and cookiecutter.use_auth_basic == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
{%- set csrf = cookie or session -%}
{#- Basic-only login-less builds: the default client sends a real Basic header. -#}
{%- set basic_client = basic and not (bearer or cookie or session) -%}
{% if basic %}import base64
{% endif %}{% if auth %}import os

# Settings are read when app.main is imported, so the test secret must be set first.
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production-" + "0123456789abcdef" * 4

from collections.abc import AsyncIterator, Awaitable, Callable
{% if rbac %}from typing import Any
{% endif %}{% else %}from collections.abc import AsyncIterator
{% endif %}
{% if cookiecutter.use_redis == "y" or auth %}import pytest
{% endif %}import pytest_asyncio
{% if cookiecutter.use_redis == "y" %}from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis
{% endif %}{% if auth %}from fastapi import FastAPI
{% endif %}from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

{% if cookiecutter.use_redis == "y" %}import app.cache.redis as redis_module
{% endif %}{% if bearer %}from app.core.security import create_access_token
{% endif %}from app.db.session import get_session
from app.main import create_app
{% if auth %}from app.models import Base, User
{% if api_key %}from app.services.api_key import ApiKeyService
{% endif %}{% if rbac %}from app.services.rbac import RbacService
{% endif %}from app.services.user import UserService
{% else %}from app.models import Base
{% endif %}

@pytest_asyncio.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
{%- if rbac %}
    # The app seeds these at startup (lifespan); the tests' app is not started.
    async with sessions() as session:
        await RbacService(session).ensure_default_roles()
{%- endif %}
    yield sessions
    await engine.dispose()
{%- if auth %}


@pytest.fixture
def application(maker: async_sessionmaker[AsyncSession]) -> FastAPI:

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    return app


MakeUser = Callable[..., Awaitable[User]]
PASSWORD = "correct-horse-battery"


@pytest.fixture
def make_user(maker: async_sessionmaker[AsyncSession]) -> MakeUser:
    """Create a user through the real UserService (argon2 hash, lowercased email)."""

    async def _make(
        email: str = "user@example.com",
        password: str = PASSWORD,
        *,
        superuser: bool = False,
        active: bool = True,
{%- if rbac %}
        roles: list[str] | None = None,
{%- endif %}
    ) -> User:
        async with maker() as session:
            return await UserService(session).create(
                email,
                password,
                is_superuser=superuser,
                is_active=active,
{%- if rbac %}
                roles=roles,
{%- endif %}
            )

    return _make
{%- if rbac %}


@pytest.fixture
def make_admin(make_user: MakeUser) -> MakeUser:
    """Create a (non-superuser) user holding the ``admin`` role."""

    async def _make(email: str = "admin@example.com", **kwargs: Any) -> User:
        return await make_user(email, roles=["admin"], **kwargs)

    return _make
{%- endif %}
{%- if api_key %}


MakeApiKey = Callable[..., Awaitable[str]]


@pytest.fixture
def make_api_key(maker: async_sessionmaker[AsyncSession]) -> MakeApiKey:
    """Create an API key for the user through the real service; returns the raw key."""

    async def _make(
        user: User, name: str = "test key", *, expires_in_days: int | None = None
    ) -> str:
        async with maker() as session:
            _, raw = await ApiKeyService(session).create(user.id, name, expires_in_days)
        return raw

    return _make
{%- endif %}
{%- if basic %}


def basic_header(email: str, password: str = PASSWORD) -> dict[str, str]:
    """``Authorization: Basic`` header for the credentials."""
    raw = base64.b64encode(f"{email}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}
{%- endif %}
{%- if bearer %}


@pytest.fixture
def auth_headers() -> Callable[[User], dict[str, str]]:
    """Authorization header carrying a real access token for the user."""

    def _headers(user: User) -> dict[str, str]:
        token, _ = create_access_token(user.id)
        return {"Authorization": f"Bearer {token}"}

    return _headers
{%- endif %}
{%- if csrf %}


CSRF_HEADER = "X-CSRF-Token"
{%- endif %}
{%- if cookie %}
MakeCookieClient = Callable[..., Awaitable[AsyncClient]]


@pytest_asyncio.fixture
async def make_cookie_client(application: FastAPI) -> AsyncIterator[MakeCookieClient]:
    """Log in through the real cookie login and return a client holding the cookies.

    The client sends the matching ``X-CSRF-Token`` header on every request by
    default; pass ``csrf_header=False`` to get one that does not (negative tests).
    """
    clients: list[AsyncClient] = []

    async def _make(
        email: str, password: str = PASSWORD, *, csrf_header: bool = True
    ) -> AsyncClient:
        c = AsyncClient(transport=ASGITransport(app=application), base_url="http://test")
        clients.append(c)
        r = await c.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password, "delivery": "cookie"},
        )
        assert r.status_code == 200, r.text
        if csrf_header:
            c.headers[CSRF_HEADER] = r.json()["csrf_token"]
        return c

    yield _make
    for c in clients:
        await c.aclose()
{%- endif %}
{%- if session %}


MakeSessionClient = Callable[..., Awaitable[AsyncClient]]


@pytest_asyncio.fixture
async def make_session_client(application: FastAPI) -> AsyncIterator[MakeSessionClient]:
    """Log in through the real session login and return a client holding the cookies.

    The client sends the matching ``X-CSRF-Token`` header on every request by
    default; pass ``csrf_header=False`` to get one that does not (negative tests).
    """
    clients: list[AsyncClient] = []

    async def _make(
        email: str, password: str = PASSWORD, *, csrf_header: bool = True
    ) -> AsyncClient:
        c = AsyncClient(transport=ASGITransport(app=application), base_url="http://test")
        clients.append(c)
        r = await c.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password, "delivery": "session"},
        )
        assert r.status_code == 200, r.text
        if csrf_header:
            c.headers[CSRF_HEADER] = r.json()["csrf_token"]
        return c

    yield _make
    for c in clients:
        await c.aclose()
{%- endif %}
{%- if rbac %}


LoginClient = Callable[[User], Awaitable[AsyncClient]]


@pytest_asyncio.fixture
async def login_client(
    application: FastAPI,
{%- if bearer %}
    auth_headers: Callable[[User], dict[str, str]],
{%- elif cookie %}
    make_cookie_client: MakeCookieClient,
{%- elif session %}
    make_session_client: MakeSessionClient,
{%- endif %}
) -> AsyncIterator[LoginClient]:
    """Client authenticated as the given user through the build's first login method."""
    clients: list[AsyncClient] = []

    async def _login(user: User) -> AsyncClient:
{%- if bearer or basic_client %}
{%- if bearer %}
        headers = auth_headers(user)
{%- else %}
        headers = basic_header(user.email)
{%- endif %}
        c = AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test", headers=headers
        )
        clients.append(c)
        return c
{%- elif cookie %}
        return await make_cookie_client(user.email)
{%- else %}
        return await make_session_client(user.email)
{%- endif %}

    yield _login
    for c in clients:
        await c.aclose()
{%- endif %}


@pytest_asyncio.fixture
async def anon_client(application: FastAPI) -> AsyncIterator[AsyncClient]:
    """Client without credentials."""
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def client(
    application: FastAPI,
    make_user: MakeUser,
{%- if bearer %}
    auth_headers: Callable[[User], dict[str, str]],
{%- elif cookie %}
    make_cookie_client: MakeCookieClient,
{%- elif session %}
    make_session_client: MakeSessionClient,
{%- endif %}
) -> AsyncIterator[AsyncClient]:
    """Client authenticated as a normal (non-superuser) user."""
    user = await make_user("member@example.com")
{%- if bearer or basic_client %}
{%- if bearer %}
    headers = auth_headers(user)
{%- else %}
    headers = basic_header(user.email)
{%- endif %}
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        yield c
{%- elif cookie %}
    # Real cookie login; sends the matching CSRF header by default.
    yield await make_cookie_client(user.email)
{%- elif session %}
    # Real session login; sends the matching CSRF header by default.
    yield await make_session_client(user.email)
{%- endif %}
{%- else %}


@pytest_asyncio.fixture
async def client(maker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    application = create_app()
    application.dependency_overrides[get_session] = override_session
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
{%- endif %}
{%- if cookiecutter.use_redis == "y" %}


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    """Every test gets its own in-process Redis; get_redis() returns it."""
    server = FakeServer()
    monkeypatch.setattr(
        redis_module, "_client", FakeRedis(server=server, decode_responses=True)
    )
    return server
{%- endif %}
