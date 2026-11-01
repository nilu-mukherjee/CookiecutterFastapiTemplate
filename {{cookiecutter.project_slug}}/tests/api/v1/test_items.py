from uuid import uuid4

from httpx import AsyncClient


async def test_v1_items_offset_pagination(client: AsyncClient) -> None:
    for i in range(5):
        r = await client.post("/api/v1/items", json={"name": f"item-{i}"})
        assert r.status_code == 201
    page = await client.get("/api/v1/items", params={"limit": 2, "offset": 2})
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 2
    assert [i["name"] for i in body["items"]] == ["item-2", "item-3"]


async def test_v1_crud(client: AsyncClient) -> None:
    created = (await client.post("/api/v1/items", json={"name": "a", "description": "d"})).json()
    item_id = created["id"]
    got = await client.get(f"/api/v1/items/{item_id}")
    assert got.status_code == 200
    assert got.json()["description"] == "d"
    patched = await client.patch(f"/api/v1/items/{item_id}", json={"name": "b"})
    assert patched.json()["name"] == "b"
    assert patched.json()["description"] == "d"
    assert (await client.delete(f"/api/v1/items/{item_id}")).status_code == 204
    assert (await client.get(f"/api/v1/items/{item_id}")).status_code == 404


async def test_item_not_found_returns_error_body_with_code_and_request_id_key(
    client: AsyncClient,
) -> None:
    r = await client.get(f"/api/v1/items/{uuid4()}")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "not_found"
    assert isinstance(body["detail"], str)
    assert "request_id" in body
