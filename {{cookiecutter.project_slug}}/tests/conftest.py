{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set bearer = auth and cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set cookie = auth and cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set session = auth and cookiecutter.use_auth_session == "y" -%}
{%- set csrf = cookie or session -%}
{% if auth %}import os

# Settings are read when app.main is imported, so the test secret must be set first.
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production-" + "0123456789abcdef" * 4

from collections.abc import AsyncIterator, Awaitable, Callable
{% else %}from collections.abc import AsyncIterator
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
from app.services.user import UserService
{% else %}from app.models import Base
{% endif %}

@pytest_asyncio.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
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
    ) -> User:
        async with maker() as session:
            return await UserService(session).create(
                email, password, is_superuser=superuser, is_active=active
            )

    return _make
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
{%- if bearer %}
    transport = ASGITransport(app=application)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=auth_headers(user)
    ) as c:
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
