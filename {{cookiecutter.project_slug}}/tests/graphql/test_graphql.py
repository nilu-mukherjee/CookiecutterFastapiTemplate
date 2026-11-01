{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
{%- set tenancy = auth and cookiecutter.use_multitenancy == "y" -%}
{%- set realtime = cookiecutter.use_sse == "y" or cookiecutter.use_long_polling == "y" or cookiecutter.use_websocket == "y" -%}
"""/graphql: the item operations over the same service, auth and tenancy as REST."""
{%- if realtime %}
import json
{%- endif %}
from datetime import datetime
from typing import Any

import pytest
{%- if realtime %}
from fastapi import FastAPI
{%- endif %}
from httpx import AsyncClient

from app.core.config import get_settings
from app.graphql.schema import create_graphql_router
{%- if realtime %}
from tests.api.v1.ws_client import Socket
{%- endif %}
{%- if rbac %}
from tests.conftest import LoginClient, MakeUser
{%- endif %}

GRAPHQL = "/graphql"
ITEMS = "/api/v1/items"
UPDATE_NAME = 'mutation($id: UUID!) { updateItem(id: $id, name: "x") { id } }'
{%- if realtime %}
PROTOCOL = "graphql-transport-ws"
QUIET = 0.3  # how long "nothing arrives" is observed for
{%- endif %}


async def run(client: AsyncClient, query: str, **variables: Any) -> dict[str, Any]:
    r = await client.post(GRAPHQL, json={"query": query, "variables": variables})
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def data(client: AsyncClient, query: str, **variables: Any) -> dict[str, Any]:
    """The ``data`` of an operation that must succeed."""
    body = await run(client, query, **variables)
    assert "errors" not in body, body
    result: dict[str, Any] = body["data"]
    return result


async def create(client: AsyncClient, name: str = "a") -> str:
    r = await client.post(ITEMS, json={"name": name})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def errors_of(body: dict[str, Any]) -> list[str]:
    assert body.get("errors"), body
    return [e["message"] for e in body["errors"]]


async def test_items_query_matches_rest_v1(client: AsyncClient) -> None:
    for name in ("one", "two", "three"):
        await create(client, name)

    rest = (await client.get(ITEMS, params={"limit": 2, "offset": 1})).json()
    got = (
        await data(
            client,
            "{ items(limit: 2, offset: 1) { total limit offset"
            " items { id name description createdAt } } }",
        )
    )["items"]

    assert (got["total"], got["limit"], got["offset"]) == (
        rest["total"],
        rest["limit"],
        rest["offset"],
    )
    assert [(i["id"], i["name"], i["description"]) for i in got["items"]] == [
        (i["id"], i["name"], i["description"]) for i in rest["items"]
    ]
    assert [datetime.fromisoformat(i["createdAt"]) for i in got["items"]] == [
        datetime.fromisoformat(i["created_at"]) for i in rest["items"]
    ]


@pytest.mark.parametrize(
    "arguments", ["limit: 0", "limit: 101", "offset: -1"]
)
async def test_items_query_rejects_out_of_range_paging(
    client: AsyncClient, arguments: str
) -> None:
    body = await run(client, "{ items(" + arguments + ") { total } }")
    assert errors_of(body)


async def test_item_query_returns_one_item_or_null(client: AsyncClient) -> None:
    item_id = await create(client, "findme")

    found = (await data(client, "query($id: UUID!) { item(id: $id) { id name } }", id=item_id))
    missing = await data(
        client,
        "query($id: UUID!) { item(id: $id) { id } }",
        id="00000000-0000-0000-0000-000000000000",
    )

    assert found["item"] == {"id": item_id, "name": "findme"}
    assert missing["item"] is None


async def test_create_update_delete_mutations(client: AsyncClient) -> None:
    created = (
        await data(
            client,
            'mutation { createItem(name: "n", description: "d") { id name description } }',
        )
    )["createItem"]
    item_id = created["id"]
    assert (created["name"], created["description"]) == ("n", "d")
    assert (await client.get(f"{ITEMS}/{item_id}")).json()["name"] == "n"  # same store as REST

    renamed = await data(
        client,
        'mutation($id: UUID!) { updateItem(id: $id, name: "m") { name description } }',
        id=item_id,
    )
    assert renamed["updateItem"] == {"name": "m", "description": "d"}  # omitted: unchanged

    cleared = await data(
        client,
        "mutation($id: UUID!) { updateItem(id: $id, description: null) { name description } }",
        id=item_id,
    )
    assert cleared["updateItem"] == {"name": "m", "description": None}

    deleted = await data(client, "mutation($id: UUID!) { deleteItem(id: $id) }", id=item_id)
    assert deleted["deleteItem"] is True
    assert (await client.get(f"{ITEMS}/{item_id}")).status_code == 404


async def test_mutation_input_is_validated_like_rest(client: AsyncClient) -> None:
    item_id = await create(client)

    empty = await run(client, 'mutation { createItem(name: "") { id } }')
    null_name = await run(
        client, "mutation($id: UUID!) { updateItem(id: $id, name: null) { id } }", id=item_id
    )

    assert errors_of(empty)
    assert errors_of(null_name)
    assert (await client.get(ITEMS)).json()["total"] == 1


async def test_missing_item_mutations_are_errors(client: AsyncClient) -> None:
    ghost = "00000000-0000-0000-0000-000000000000"
    update = await run(client, UPDATE_NAME, id=ghost)
    delete = await run(client, "mutation($id: UUID!) { deleteItem(id: $id) }", id=ghost)

    assert errors_of(update) == ["Item not found"]
    assert errors_of(delete) == ["Item not found"]


async def test_domain_errors_are_not_logged_as_faults(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    await run(
        client,
        "mutation($id: UUID!) { deleteItem(id: $id) }",
        id="00000000-0000-0000-0000-000000000000",
    )
    assert [r for r in caplog.records if r.levelname == "ERROR"] == []


async def test_query_depth_over_10_is_rejected(client: AsyncClient) -> None:
    nested = "items { " * 12 + "total" + " }" * 12
    body = await run(client, "{ " + nested + " }")
    assert any("depth" in message for message in errors_of(body))


async def test_introspection_and_graphiql_only_outside_prod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    introspection = "{ __schema { queryType { name } } }"

    dev = create_graphql_router()
    assert dev.graphql_ide == "graphiql"
    assert (await dev.schema.execute(introspection)).errors is None

    prod_settings = get_settings().model_copy(update={"environment": "prod"})
    monkeypatch.setattr("app.graphql.schema.get_settings", lambda: prod_settings)
    prod = create_graphql_router()
    assert prod.graphql_ide is None
    assert (await prod.schema.execute(introspection)).errors


async def test_graphql_is_not_part_of_the_versioned_api(client: AsyncClient) -> None:
    r = await client.post("/api/v1/graphql", json={"query": "{ __typename }"})
    assert r.status_code == 404
{%- if auth %}


async def test_unauthenticated_query_is_401(anon_client: AsyncClient) -> None:
    r = await anon_client.post(GRAPHQL, json={"query": "{ items { total } }"})
    assert r.status_code == 401
    assert r.json()["code"] in {"not_authenticated", "invalid_credentials"}


async def test_unauthenticated_mutation_is_401_and_changes_nothing(
    anon_client: AsyncClient, client: AsyncClient
) -> None:
    r = await anon_client.post(
        GRAPHQL, json={"query": 'mutation { createItem(name: "x") { id } }'}
    )
    assert r.status_code == 401
    assert (await client.get(ITEMS)).json()["total"] == 0
{%- endif %}
{%- if rbac %}


async def test_missing_permission_errors(
    make_user: MakeUser, login_client: LoginClient, client: AsyncClient
) -> None:
    nobody = await login_client(await make_user("nobody@example.com", roles=[]))
    item_id = await create(client)

    read = await run(nobody, "{ items { total } }")
    one = await run(nobody, "query($id: UUID!) { item(id: $id) { id } }", id=item_id)
    write = await run(nobody, 'mutation { createItem(name: "x") { id } }')
    delete = await run(nobody, "mutation($id: UUID!) { deleteItem(id: $id) }", id=item_id)

    for body in (read, one, write, delete):
        assert any("items:" in m for m in errors_of(body))
    assert (await client.get(ITEMS)).json()["total"] == 1
{%- endif %}
{%- if tenancy %}


async def test_cross_tenant_item_is_null(client: AsyncClient, client_b: AsyncClient) -> None:
    b_item = await create(client_b, "b-secret")

    seen = await data(client, "query($id: UUID!) { item(id: $id) { id } }", id=b_item)
    listed = await data(client, "{ items { total items { id } } }")
    updated = await run(client, UPDATE_NAME, id=b_item)
    deleted = await run(client, "mutation($id: UUID!) { deleteItem(id: $id) }", id=b_item)

    assert seen["item"] is None
    assert listed["items"] == {"total": 0, "items": []}
    assert errors_of(updated) == ["Item not found"]
    assert errors_of(deleted) == ["Item not found"]
    assert (await client_b.get(f"{ITEMS}/{b_item}")).json()["name"] == "b-secret"
{%- endif %}
{%- if realtime %}


SUBSCRIBE = {
    "id": "1",
    "type": "subscribe",
    "payload": {"query": "subscription { itemEvents { id type itemId } }"},
}


async def subscribe(application: FastAPI, client: AsyncClient, **kwargs: Any) -> Socket:
    """Open a graphql-transport-ws socket and send connection_init (not yet subscribed)."""
    socket = Socket(application, client, GRAPHQL, subprotocols=[PROTOCOL], **kwargs)
    await socket.__aenter__()
    if socket.accepted:
        await socket.send_text(json.dumps({"type": "connection_init"}))
        assert (await socket.receive_json())["type"] == "connection_ack"
        await socket.send_text(json.dumps(SUBSCRIBE))
    return socket


async def test_item_events_subscription_receives_created_event(
    application: FastAPI, client: AsyncClient
) -> None:
    socket = await subscribe(application, client)
    try:
        assert socket.accepted
        item_id = await create(client)

        message = await socket.receive_json()
    finally:
        await socket.close()

    assert message["type"] == "next"
    event = message["payload"]["data"]["itemEvents"]
    assert event["type"] == "item.created"
    assert event["itemId"] == item_id
    assert event["id"]


async def test_subscription_starts_from_now_and_follows_updates_and_deletes(
    application: FastAPI, client: AsyncClient
) -> None:
    before = await create(client, "before")
    socket = await subscribe(application, client)
    try:
        with pytest.raises(TimeoutError):
            await socket.receive_text(QUIET)
        await client.patch(f"{ITEMS}/{before}", json={"name": "after"})
        await client.delete(f"{ITEMS}/{before}")

        events = [(await socket.receive_json())["payload"]["data"]["itemEvents"] for _ in range(2)]
    finally:
        await socket.close()

    assert [e["type"] for e in events] == ["item.updated", "item.deleted"]


async def test_closing_a_subscription_releases_its_slot(
    application: FastAPI, client: AsyncClient
) -> None:
    socket = await subscribe(application, client)
    assert socket.accepted
    await create(client)
    await socket.receive_json()
    assert application.state.realtime.active == 1

    await socket.close()

    assert application.state.realtime.active == 0


async def test_subscription_from_a_foreign_origin_is_refused(
    application: FastAPI, client: AsyncClient
) -> None:
    socket = await subscribe(application, client, headers={"Origin": "https://evil.example"})
    try:
        assert not socket.accepted
        assert socket.close_code == 1008
    finally:
        await socket.close()
{%- if auth %}


async def test_subscription_without_credentials_is_refused(
    application: FastAPI, anon_client: AsyncClient
) -> None:
    socket = await subscribe(application, anon_client)
    try:
        assert not socket.accepted
        assert socket.close_code == 1008
    finally:
        await socket.close()
{%- endif %}
{%- if rbac %}


async def test_subscription_without_items_read_errors(
    application: FastAPI, make_user: MakeUser, login_client: LoginClient, client: AsyncClient
) -> None:
    nobody = await login_client(await make_user("nobody@example.com", roles=[]))
    socket = await subscribe(application, nobody)
    try:
        message = await socket.receive_json()
        await create(client)
    finally:
        await socket.close()

    # An error raised by the subscription arrives as a message with errors and no data.
    assert message["type"] == "next"
    assert "items:read" in message["payload"]["errors"][0]["message"]
{%- endif %}
{%- if tenancy %}


async def test_subscription_never_receives_other_tenant_events(
    application: FastAPI, client: AsyncClient, client_b: AsyncClient
) -> None:
    socket_a = await subscribe(application, client)
    socket_b = await subscribe(application, client_b)
    try:
        a_item = await create(client, "a-secret")

        event = (await socket_a.receive_json())["payload"]["data"]["itemEvents"]
        with pytest.raises(TimeoutError):
            await socket_b.receive_text(QUIET)
    finally:
        await socket_a.close()
        await socket_b.close()

    assert event["itemId"] == a_item
{%- endif %}
{%- endif %}
