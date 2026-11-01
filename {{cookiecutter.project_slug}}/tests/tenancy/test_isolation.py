"""Cross-tenant isolation of the example resource, for every enabled auth method.

The tenant of a request is the authenticated user's tenant, read from the database.
Another tenant's rows must look exactly like missing ones (404, never 403).
"""
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Item, Tenant
from tests.conftest import MakeUser
from tests.tenancy.conftest import METHODS, MethodClient

BASES = ["/api/v1/items", "/api/v2/items"]


def spoof(tenant: Tenant) -> dict[str, str]:
    """Headers a client might use to try to pick a tenant; they must change nothing."""
    return {"X-Tenant-ID": str(tenant.id), "X-Tenant": tenant.slug}


async def create_item(client: AsyncClient, name: str, base: str = "/api/v1/items") -> str:
    r = await client.post(base, json={"name": name})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def v2_walk(client: AsyncClient, **params: str) -> list[str]:
    """Every item name reachable by following the v2 cursor from the first page."""
    names: list[str] = []
    cursor: str | None = None
    while True:
        query: dict[str, str | int] = {"limit": 2, **params}
        if cursor:
            query["cursor"] = cursor
        body = (await client.get("/api/v2/items", params=query)).json()
        names.extend(i["name"] for i in body["data"])
        cursor = body["next_cursor"]
        if cursor is None:
            return names


async def test_principal_tenant_is_the_users_tenant(
    client: AsyncClient, client_b: AsyncClient, tenant: Tenant, tenant_b: Tenant
) -> None:
    assert (await client.get("/api/v1/users/me")).json()["tenant_id"] == str(tenant.id)
    assert (await client_b.get("/api/v1/users/me")).json()["tenant_id"] == str(tenant_b.id)


async def test_tenant_headers_and_query_param_are_ignored(
    client: AsyncClient, client_b: AsyncClient, tenant: Tenant, tenant_b: Tenant
) -> None:
    await create_item(client_b, "b-secret")
    me = await client.get(
        "/api/v1/users/me", headers=spoof(tenant_b), params={"tenant_id": str(tenant_b.id)}
    )
    assert me.json()["tenant_id"] == str(tenant.id)
    listed = await client.get(
        "/api/v1/items", headers=spoof(tenant_b), params={"tenant_id": str(tenant_b.id)}
    )
    assert listed.json()["total"] == 0
    assert await v2_walk(client, tenant_id=str(tenant_b.id)) == []


@pytest.mark.parametrize("base", BASES)
async def test_tenant_a_cannot_get_tenant_b_item_404(
    client: AsyncClient, client_b: AsyncClient, tenant_b: Tenant, base: str
) -> None:
    item_id = await create_item(client_b, "b-secret", base)
    for r in (
        await client.get(f"{base}/{item_id}"),
        await client.get(f"{base}/{item_id}", headers=spoof(tenant_b)),
        await client.patch(f"{base}/{item_id}", json={"name": "stolen"}),
        await client.delete(f"{base}/{item_id}"),
    ):
        assert r.status_code == 404, r.text
        assert r.json()["code"] == "not_found"
    # Untouched for its owner.
    still = await client_b.get(f"{base}/{item_id}")
    assert still.status_code == 200
    assert still.json()["name"] == "b-secret"


async def test_list_returns_only_own_tenant(client: AsyncClient, client_b: AsyncClient) -> None:
    for i in range(3):
        await create_item(client, f"a-{i}")
    for i in range(4):
        await create_item(client_b, f"b-{i}")
    page = (await client.get("/api/v1/items", params={"limit": 100})).json()
    assert page["total"] == 3
    assert sorted(i["name"] for i in page["items"]) == ["a-0", "a-1", "a-2"]
    assert sorted(await v2_walk(client)) == ["a-0", "a-1", "a-2"]
    assert sorted(await v2_walk(client_b)) == ["b-0", "b-1", "b-2", "b-3"]
    assert (await client_b.get("/api/v1/items")).json()["total"] == 4


async def test_create_sets_tenant_from_principal_ignoring_body_and_header(
    client: AsyncClient,
    client_b: AsyncClient,
    tenant: Tenant,
    tenant_b: Tenant,
    maker: async_sessionmaker[AsyncSession],
) -> None:
    for base in BASES:
        r = await client.post(
            base,
            json={"name": "mine", "tenant_id": str(tenant_b.id)},
            headers=spoof(tenant_b),
        )
        assert r.status_code == 201
        async with maker() as session:
            row = await session.scalar(select(Item).where(Item.id == UUID(r.json()["id"])))
        assert row is not None
        assert row.tenant_id == tenant.id
        assert (await client_b.get(f"{base}/{r.json()['id']}")).status_code == 404
    assert (await client_b.get("/api/v1/items")).json()["total"] == 0


@pytest.mark.parametrize("method", METHODS)
async def test_isolation_holds_for_every_enabled_auth_method(
    method: str,
    method_client: MethodClient,
    make_user: MakeUser,
    client_b: AsyncClient,
    tenant: Tenant,
) -> None:
    """The same checks through each enabled method's real credential."""
    b_item = await create_item(client_b, "b-secret")
    a = await method_client(method, await make_user(f"{method}@example.com", tenant=tenant))
    a_item = await create_item(a, f"via-{method}")
    for base in BASES:
        assert (await a.get(f"{base}/{b_item}")).status_code == 404
        assert (await a.patch(f"{base}/{b_item}", json={"name": "x"})).status_code == 404
        assert (await a.delete(f"{base}/{b_item}")).status_code == 404
        assert (await client_b.get(f"{base}/{a_item}")).status_code == 404
    assert [i["name"] for i in (await a.get("/api/v1/items")).json()["items"]] == [
        f"via-{method}"
    ]
    assert await v2_walk(a) == [f"via-{method}"]
    assert (await client_b.get(f"/api/v1/items/{b_item}")).json()["name"] == "b-secret"


async def test_superuser_cannot_read_another_tenants_items(
    method_client: MethodClient,
    make_user: MakeUser,
    client_b: AsyncClient,
    system_tenant: Tenant,
) -> None:
    """A superuser operates the platform (tenants), not other tenants' data."""
    b_item = await create_item(client_b, "b-secret")
    root = await method_client(
        METHODS[0], await make_user("root@example.com", superuser=True, tenant=system_tenant)
    )
    for base in BASES:
        assert (await root.get(f"{base}/{b_item}")).status_code == 404
        assert (await root.delete(f"{base}/{b_item}")).status_code == 404
    assert (await root.get("/api/v1/items")).json()["total"] == 0
    assert await v2_walk(root) == []
