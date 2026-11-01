{%- set bearer = cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set cookie = cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set session = cookiecutter.use_auth_session == "y" -%}
{%- set login = bearer or cookie or session -%}
"""A deactivated tenant locks out its users with every method, on the next request."""
import pytest
import pytest_asyncio
from httpx import AsyncClient, Response

from app.models import Tenant
from tests.conftest import {% if login %}PASSWORD, {% endif %}MakeTenant, MakeUser
from tests.tenancy.conftest import METHODS, MethodClient

TENANTS = "/api/v1/tenants"


@pytest_asyncio.fixture
async def root(
    method_client: MethodClient, make_user: MakeUser, system_tenant: Tenant
) -> AsyncClient:
    user = await make_user("root@example.com", superuser=True, tenant=system_tenant)
    return await method_client(METHODS[0], user)


def assert_rejected(r: Response) -> None:
    assert r.status_code == 401, r.text
    assert r.json()["code"] == "invalid_credentials"


@pytest.mark.parametrize("method", METHODS)
async def test_deactivating_a_tenant_rejects_existing_credentials(
    method: str,
    method_client: MethodClient,
    make_user: MakeUser,
    root: AsyncClient,
    client_b: AsyncClient,
    tenant: Tenant,
) -> None:
    a = await method_client(method, await make_user(f"{method}@example.com", tenant=tenant))
    assert (await a.get("/api/v1/items")).status_code == 200
    r = await root.patch(f"{TENANTS}/{tenant.id}", json={"is_active": False})
    assert r.status_code == 200
    assert r.json()["is_active"] is False
    assert_rejected(await a.get("/api/v1/items"))
    assert_rejected(await a.get("/api/v1/users/me"))
    # Other tenants are unaffected.
    assert (await client_b.get("/api/v1/items")).status_code == 200
{%- if login %}


async def test_user_of_an_inactive_tenant_cannot_log_in(
    anon_client: AsyncClient, make_tenant: MakeTenant, make_user: MakeUser
) -> None:
    dormant = await make_tenant("Dormant", active=False)
    await make_user("sleeper@example.com", tenant=dormant)
    login = await anon_client.post(
        "/api/v1/auth/login", json={"email": "sleeper@example.com", "password": PASSWORD}
    )
    wrong = await anon_client.post(
        "/api/v1/auth/login", json={"email": "sleeper@example.com", "password": "wrong-password"}
    )
    assert_rejected(login)
    assert login.json()["detail"] == wrong.json()["detail"]
{%- endif %}
{%- if bearer %}


async def test_refresh_token_of_a_deactivated_tenant_is_rejected(
    anon_client: AsyncClient, make_user: MakeUser, root: AsyncClient, tenant: Tenant
) -> None:
    await make_user("r@example.com", tenant=tenant)
    tokens = (
        await anon_client.post(
            "/api/v1/auth/login",
            json={"email": "r@example.com", "password": PASSWORD{% if cookie or session %}, "delivery": "bearer"{% endif %}},
        )
    ).json()
    await root.patch(f"{TENANTS}/{tenant.id}", json={"is_active": False})
    r = await anon_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert_rejected(r)
{%- endif %}
{%- if cookie %}


async def test_refresh_cookie_of_a_deactivated_tenant_is_rejected(
    method_client: MethodClient, make_user: MakeUser, root: AsyncClient, tenant: Tenant
) -> None:
    c = await method_client("jwt_cookie", await make_user("rc@example.com", tenant=tenant))
    await root.patch(f"{TENANTS}/{tenant.id}", json={"is_active": False})
    assert_rejected(await c.post("/api/v1/auth/refresh"))
{%- endif %}
