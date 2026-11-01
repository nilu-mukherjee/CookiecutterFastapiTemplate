{%- set tenancy = cookiecutter.use_multitenancy == "y" -%}
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.rbac import RbacService
from tests.conftest import MakeApiKey, MakeUser

ITEMS = "/api/v1/items"
USERS = "/api/v1/users"


def _x(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


async def test_api_key_inherits_owner_permissions(
    anon_client: AsyncClient, make_user: MakeUser, make_admin: MakeUser, make_api_key: MakeApiKey
) -> None:
    member_key = await make_api_key(await make_user("member2@example.com"))
    admin_key = await make_api_key(await make_admin())
    assert (await anon_client.get(ITEMS, headers=_x(member_key))).status_code == 200
    created = await anon_client.post(ITEMS, json={"name": "x"}, headers=_x(member_key))
    assert created.status_code == 201
    denied = await anon_client.get(USERS, headers=_x(member_key))
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"
    assert (await anon_client.get(USERS, headers=_x(admin_key))).status_code == 200


async def test_api_key_follows_owner_role_changes_without_a_new_key(
    anon_client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    make_user: MakeUser,
    make_api_key: MakeApiKey,
) -> None:
    owner = await make_user("owner@example.com")
    key = await make_api_key(owner)
    assert (await anon_client.get(ITEMS, headers=_x(key))).status_code == 200
    async with maker() as session:
        await RbacService(session{% if tenancy %}, owner.tenant_id{% endif %}).set_user_roles(owner.id, [])
    assert (await anon_client.get(ITEMS, headers=_x(key))).status_code == 403
    async with maker() as session:
        await RbacService(session{% if tenancy %}, owner.tenant_id{% endif %}).set_user_roles(owner.id, ["admin"])
    assert (await anon_client.get(USERS, headers=_x(key))).status_code == 200
