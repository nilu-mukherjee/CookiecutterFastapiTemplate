{%- set rbac = cookiecutter.use_rbac == "y" -%}
"""Users belong to exactly one tenant; emails are unique across all tenants."""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import AppError
from app.models import Tenant
from app.services.user import UserService
from tests.conftest import MakeUser
from tests.tenancy.conftest import METHODS, MethodClient

USERS = "/api/v1/users"


@pytest_asyncio.fixture
async def manager(
    method_client: MethodClient, make_user: MakeUser, {% if rbac %}tenant: Tenant{% else %}system_tenant: Tenant{% endif %}
) -> AsyncClient:
{%- if rbac %}
    """A tenant admin (``users:read``/``users:write``) of the primary tenant."""
    admin = await make_user("admin-a@example.com", roles=["admin"], tenant=tenant)
{%- else %}
    """Without RBAC only a superuser manages users; a superuser lives in the system tenant."""
    admin = await make_user("root@example.com", superuser=True, tenant=system_tenant)
{%- endif %}
    return await method_client(METHODS[0], admin)


async def test_users_list_shows_only_own_tenant(
    manager: AsyncClient,
    make_user: MakeUser,
    tenant_b: Tenant,
) -> None:
    await make_user("other-b@example.com", tenant=tenant_b)
    me = (await manager.get(f"{USERS}/me")).json()
    body = (await manager.get(USERS, params={"limit": 100})).json()
    emails = {u["email"] for u in body["items"]}
    assert "other-b@example.com" not in emails
    assert {u["tenant_id"] for u in body["items"]} == {me["tenant_id"]}
    assert body["total"] == len(body["items"])


async def test_created_user_lives_in_the_callers_tenant(manager: AsyncClient) -> None:
    me = (await manager.get(f"{USERS}/me")).json()
    r = await manager.post(USERS, json={"email": "new@example.com", "password": "long-enough-pw"})
    assert r.status_code == 201
    assert r.json()["tenant_id"] == me["tenant_id"]


async def test_email_is_unique_across_tenants_with_the_same_409(
    manager: AsyncClient, make_user: MakeUser, tenant_b: Tenant
) -> None:
    await make_user("taken-b@example.com", tenant=tenant_b)
    me = (await manager.get(f"{USERS}/me")).json()
    other = await manager.post(
        USERS, json={"email": "Taken-B@example.com", "password": "long-enough-pw"}
    )
    same = await manager.post(USERS, json={"email": me["email"], "password": "long-enough-pw"})
    assert other.status_code == same.status_code == 409
    strip = {"request_id"}
    assert {k: v for k, v in other.json().items() if k not in strip} == {
        k: v for k, v in same.json().items() if k not in strip
    }
    assert "tenant" not in other.text.lower()


async def test_superuser_can_only_be_created_in_the_system_tenant(
    maker: async_sessionmaker[AsyncSession], tenant: Tenant, system_tenant: Tenant
) -> None:
    async with maker() as session:
        with pytest.raises(AppError) as exc:
            await UserService(session, tenant.id).create(
                "boss@example.com", "long-enough-pw", is_superuser=True
            )
    assert (exc.value.status_code, exc.value.code) == (403, "forbidden")
    async with maker() as session:
        boss = await UserService(session, system_tenant.id).create(
            "boss@example.com", "long-enough-pw", is_superuser=True
        )
    assert boss.tenant_id == system_tenant.id
