{%- set tenancy = cookiecutter.use_auth == "y" and cookiecutter.use_multitenancy == "y" -%}
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

{% if tenancy %}from app.models import Tenant
{% endif %}from app.models.item import Item


async def test_v2_items_cursor_pagination_walks_all_pages(client: AsyncClient) -> None:
    created = set()
    for i in range(7):
        r = await client.post("/api/v2/items", json={"name": f"item-{i}"})
        assert r.status_code == 201
        created.add(r.json()["id"])

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str | int] = {"limit": 3}
        if cursor:
            params["cursor"] = cursor
        r = await client.get("/api/v2/items", params=params)
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"data", "next_cursor"}
        seen.extend(i["id"] for i in body["data"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert pages == 3
    assert len(seen) == len(set(seen)) == 7
    assert set(seen) == created


async def test_v2_invalid_cursor_is_400(client: AsyncClient) -> None:
    r = await client.get("/api/v2/items", params={"cursor": "not-a-cursor"})
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_cursor"


async def test_v1_and_v2_share_one_store(client: AsyncClient) -> None:
    created = (await client.post("/api/v1/items", json={"name": "shared"})).json()
    got = await client.get(f"/api/v2/items/{created['id']}")
    assert got.status_code == 200
    assert got.json()["name"] == "shared"
    listing = (await client.get("/api/v2/items")).json()
    assert [i["id"] for i in listing["data"]] == [created["id"]]


async def test_v2_patch_null_name_is_422(client: AsyncClient) -> None:
    created = (await client.post("/api/v2/items", json={"name": "a"})).json()
    r = await client.patch(f"/api/v2/items/{created['id']}", json={"name": None})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert "name" in body["detail"]
    assert "request_id" in body


async def test_v2_patch_null_description_clears_it(client: AsyncClient) -> None:
    created = (
        await client.post("/api/v2/items", json={"name": "a", "description": "d"})
    ).json()
    r = await client.patch(f"/api/v2/items/{created['id']}", json={"description": None})
    assert r.status_code == 200
    assert r.json()["description"] is None
    assert r.json()["name"] == "a"


async def test_v2_cursor_tie_break_with_identical_created_at(
{%- if tenancy %}
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], tenant: Tenant
{%- else %}
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
{%- endif %}
) -> None:
    same = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    async with maker() as session:
{%- if tenancy %}
        # In the client user's tenant, so the client can see them.
        session.add_all(
            [Item(name=f"t-{i}", created_at=same, tenant_id=tenant.id) for i in range(7)]
        )
{%- else %}
        session.add_all([Item(name=f"t-{i}", created_at=same) for i in range(7)])
{%- endif %}
        await session.commit()

    async def walk() -> list[str]:
        seen: list[str] = []
        cursor: str | None = None
        while True:
            params: dict[str, str | int] = {"limit": 2}
            if cursor:
                params["cursor"] = cursor
            body = (await client.get("/api/v2/items", params=params)).json()
            seen.extend(i["id"] for i in body["data"])
            cursor = body["next_cursor"]
            if cursor is None:
                return seen

    first = await walk()
    second = await walk()
    assert len(first) == len(set(first)) == 7
    assert first == second
