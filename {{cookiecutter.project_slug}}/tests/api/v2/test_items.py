from httpx import AsyncClient


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
