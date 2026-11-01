from httpx import AsyncClient


async def test_live_and_ready_ok(client: AsyncClient) -> None:
    live = await client.get("/health/live")
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok"}


async def test_docs_per_version(client: AsyncClient) -> None:
    for version in ("v1", "v2"):
        docs = await client.get(f"/api/{version}/docs")
        assert docs.status_code == 200
        schema = await client.get(f"/api/{version}/openapi.json")
        assert schema.status_code == 200
        assert "/items" in schema.json()["paths"]
    v1 = (await client.get("/api/v1/openapi.json")).json()
    v2 = (await client.get("/api/v2/openapi.json")).json()
    assert v1["info"]["version"] == "1"
    assert v2["info"]["version"] == "2"
