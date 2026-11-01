{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
{%- set tenancy = auth and cookiecutter.use_multitenancy == "y" -%}
{%- set redis = cookiecutter.use_redis == "y" -%}
"""GET /api/v1/items/poll: HTTP long polling of the item events."""
import asyncio
import logging
import time
from collections.abc import {% if auth %}AsyncIterator, {% endif %}Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
{%- if auth %}
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
{%- endif %}

import app.api.v1.endpoints.poll as poll_module
from app.core.config import get_settings
{%- if auth %}
from app.db.session import get_session
{%- endif %}
{%- if tenancy %}
from app.models import Tenant
{%- endif %}
from app.realtime.broker import BrokerUnavailable, EventBroker, channel_for
from app.realtime.state import RealtimeState
{%- if rbac or tenancy %}
from tests.conftest import {% if rbac %}LoginClient, {% endif %}MakeUser
{%- endif %}
{%- if tenancy %}
from tests.tenancy import conftest as tenancy_clients
from tests.tenancy.conftest import METHODS, MethodClient
{%- endif %}

POLL = "/api/v1/items/poll"
ITEMS = "/api/v1/items"
WAIT = 2.0

{%- if tenancy %}


# The tenancy tests' per-auth-method clients, reused here (fixtures are per directory).
method_client = tenancy_clients.method_client
{%- endif %}


@pytest.fixture
def broker(application: FastAPI) -> EventBroker:
    broker: EventBroker = application.state.broker
    return broker
{%- if tenancy %}


@pytest.fixture
def channel(tenant: Tenant) -> str:
    return channel_for(tenant.id)
{%- else %}


@pytest.fixture
def channel() -> str:
    return channel_for(None)
{%- endif %}


{%- if auth %}


@pytest.fixture
def sessions(
    application: FastAPI, maker: async_sessionmaker[AsyncSession]
) -> list[AsyncSession]:
    """Every database session the app opens for a request, to see which are still in use."""
    opened: list[AsyncSession] = []

    async def tracked() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            opened.append(session)
            yield session

    application.dependency_overrides[get_session] = tracked
    return opened
{%- endif %}


@pytest.fixture
def max_wait(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("LONG_POLL_MAX_SECONDS", "0.2")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def create(client: AsyncClient, name: str = "a") -> str:
    r = await client.post(ITEMS, json={"name": name})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def test_without_after_returns_the_cursor_immediately(
    client: AsyncClient, broker: EventBroker, channel: str
) -> None:
    empty = await client.get(POLL)
    assert empty.status_code == 200
    assert empty.json() == {"events": [], "last_id": None}

    await create(client)
    newest = await broker.latest_id(channel)
    r = await client.get(POLL, params={"timeout": 30})

    assert r.json() == {"events": [], "last_id": newest}


async def test_returns_immediately_when_events_exist(
    client: AsyncClient, broker: EventBroker
) -> None:
    first = await create(client, "one")
    second = await create(client, "two")

    began = time.monotonic()
    r = await client.get(POLL, params={"after": broker.start_id, "timeout": 10})

    assert time.monotonic() - began < WAIT
    assert r.status_code == 200
    body = r.json()
    assert [(e["type"], e["data"]) for e in body["events"]] == [
        ("item.created", {"id": first}),
        ("item.created", {"id": second}),
    ]
    assert set(body["events"][0]) == {"id", "type", "data"}
    assert body["last_id"] == body["events"][-1]["id"]


async def test_after_the_last_id_only_new_events_are_returned(
    client: AsyncClient, broker: EventBroker
) -> None:
    await create(client, "one")
    cursor = (await client.get(POLL)).json()["last_id"]
    two = await create(client, "two")

    r = await client.get(POLL, params={"after": cursor, "timeout": 1})

    assert [e["data"] for e in r.json()["events"]] == [{"id": two}]


async def test_held_until_an_event_is_published(
    client: AsyncClient, broker: EventBroker
) -> None:
    waiting = asyncio.create_task(
        client.get(POLL, params={"after": broker.start_id, "timeout": 10})
    )
    await asyncio.sleep(0.1)
    assert not waiting.done()

    item_id = await create(client)
    r = await asyncio.wait_for(waiting, WAIT)

    assert r.status_code == 200
    assert [e["data"] for e in r.json()["events"]] == [{"id": item_id}]


async def test_timeout_without_events_is_204_with_no_body(
    client: AsyncClient, broker: EventBroker
) -> None:
    r = await client.get(POLL, params={"after": broker.start_id, "timeout": 0.1})

    assert r.status_code == 204
    assert r.content == b""


async def test_timeout_zero_does_not_wait(client: AsyncClient, broker: EventBroker) -> None:
    began = time.monotonic()
    r = await client.get(POLL, params={"after": broker.start_id, "timeout": 0})

    assert r.status_code == 204
    assert time.monotonic() - began < 1


@pytest.mark.usefixtures("max_wait")
async def test_timeout_is_clamped_to_the_configured_maximum(
    client: AsyncClient, broker: EventBroker
) -> None:
    began = time.monotonic()
    r = await asyncio.wait_for(
        client.get(POLL, params={"after": broker.start_id, "timeout": 3600}), WAIT
    )

    assert r.status_code == 204
    assert time.monotonic() - began < WAIT


@pytest.mark.parametrize("bad", ["abc", "-1", "nan", ""])
async def test_bad_timeout_is_422(client: AsyncClient, broker: EventBroker, bad: str) -> None:
    r = await client.get(POLL, params={"after": broker.start_id, "timeout": bad})

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


@pytest.mark.parametrize("bad", ["abc", "-1", "1.5", "1-0-0", "x" * 100, ""])
async def test_invalid_after_is_400(client: AsyncClient, bad: str) -> None:
    r = await client.get(POLL, params={"after": bad, "timeout": 0})

    assert r.status_code == 400
    assert r.json()["code"] == "invalid_event_id"


async def test_poll_route_is_not_swallowed_by_the_item_route(client: AsyncClient) -> None:
    # Registered after /items/{item_id}, "poll" would be parsed as an item id (422).
    assert (await client.get(POLL)).status_code == 200


async def test_v2_has_no_poll(client: AsyncClient) -> None:
    assert (await client.get("/api/v2/items/poll")).status_code in (404, 422)


@pytest.mark.parametrize("params", [{}, {"after": "0-0", "timeout": 0}])
async def test_broker_outage_is_503_events_unavailable(
    client: AsyncClient,
    broker: EventBroker,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    params: dict[str, Any],
) -> None:
    async def down(*args: Any, **kwargs: Any) -> Any:
        raise BrokerUnavailable("redis://user:secret@host:6379 refused")

    monkeypatch.setattr(broker, "read", down)
    monkeypatch.setattr(broker, "latest_id", down)

    with caplog.at_level(logging.WARNING):
        r = await client.get(POLL, params=params)

    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "events_unavailable"
    assert "secret" not in r.text and "redis" not in r.text.lower()
    assert "secret" not in caplog.text
{%- if redis %}


@pytest.mark.parametrize("params", [{}, {"after": "0-0", "timeout": 0}])
async def test_redis_outage_is_503_events_unavailable(
    client: AsyncClient, fake_redis: Any, params: dict[str, Any]
) -> None:
    fake_redis.connected = False

    r = await client.get(POLL, params=params)

    assert r.status_code == 503
    assert r.json()["code"] == "events_unavailable"
{%- endif %}
{%- if auth %}


async def test_unauthenticated_is_401(anon_client: AsyncClient) -> None:
    assert (await anon_client.get(POLL)).status_code == 401
    assert (await anon_client.get(POLL, params={"after": "0-0", "timeout": 0})).status_code == 401
{%- endif %}
{%- if rbac %}


async def test_without_items_read_is_403(
    make_user: MakeUser, login_client: LoginClient
) -> None:
    nobody = await login_client(await make_user("nobody@example.com", roles=[]))

    r = await nobody.get(POLL)

    assert r.status_code == 403
    assert r.json()["code"] == "permission_denied"
    assert "items:read" in r.json()["detail"]
{%- endif %}
{%- if tenancy %}


async def test_tenants_polls_are_isolated(
    client: AsyncClient, client_b: AsyncClient, broker: EventBroker
) -> None:
    a_cursor = b_cursor = broker.start_id
    # B's long poll is open while A writes; only A's poll is released.
    b_poll = asyncio.create_task(client_b.get(POLL, params={"after": b_cursor, "timeout": 0.5}))
    a_poll = asyncio.create_task(client.get(POLL, params={"after": a_cursor, "timeout": 5}))
    await asyncio.sleep(0.1)

    a_item = await create(client, "a-secret")

    a_response = await asyncio.wait_for(a_poll, WAIT)
    b_response = await asyncio.wait_for(b_poll, WAIT)
    assert [e["data"] for e in a_response.json()["events"]] == [{"id": a_item}]
    assert b_response.status_code == 204
    assert "a-secret" not in b_response.text


async def test_another_tenants_cursor_gets_nothing_of_that_tenant(
    client: AsyncClient, client_b: AsyncClient, broker: EventBroker, tenant: Tenant
) -> None:
    await create(client, "a-one")
    await create(client, "a-two")
    a_first = (await broker.read(channel_for(tenant.id), broker.start_id, 0))[0]

    r = await client_b.get(POLL, params={"after": a_first.id, "timeout": 0})
    assert r.status_code == 204

    b_item = await create(client_b, "b-one")
    r = await client_b.get(POLL, params={"after": broker.start_id, "timeout": 0})
    assert [e["data"] for e in r.json()["events"]] == [{"id": b_item}]


async def test_system_tenant_superuser_does_not_see_tenant_events(
    client: AsyncClient,
    make_user: MakeUser,
    method_client: MethodClient,
    broker: EventBroker,
    system_tenant: Tenant,
) -> None:
    root = await method_client(
        METHODS[0],
        await make_user("root@example.com", superuser=True, tenant=system_tenant)
    )
    await create(client, "tenant-data")

    r = await root.get(POLL, params={"after": broker.start_id, "timeout": 0})

    assert r.status_code == 204
    assert (await root.get(POLL)).json() == {"events": [], "last_id": None}
{%- endif %}


async def test_closing_ends_a_waiting_poll(
    application: FastAPI,
    client: AsyncClient,
    broker: EventBroker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(poll_module, "WAIT_SLICE_SECONDS", 0.05)
    waiting = asyncio.create_task(
        client.get(POLL, params={"after": broker.start_id, "timeout": 30})
    )
    await asyncio.sleep(0.2)
    assert not waiting.done()

    application.state.realtime.closing.set()
    r = await asyncio.wait_for(waiting, WAIT)

    assert r.status_code == 204


async def test_too_many_polls_is_503_with_retry_after_and_slots_are_released(
    application: FastAPI, client: AsyncClient, broker: EventBroker
) -> None:
    application.state.realtime = RealtimeState(1)
    waiting = asyncio.create_task(
        client.get(POLL, params={"after": broker.start_id, "timeout": 10})
    )
    await asyncio.sleep(0.1)

    r = await client.get(POLL, params={"after": broker.start_id, "timeout": 10})

    assert r.status_code == 503
    assert r.headers["retry-after"] == "5"
    assert r.json()["code"] == "too_many_connections"
    await create(client)
    assert (await asyncio.wait_for(waiting, WAIT)).status_code == 200
    assert application.state.realtime.active == 0
    # A refused-before-waiting request (bad id) gives its slot back too.
    assert (await client.get(POLL, params={"after": "x"})).status_code == 400
    assert application.state.realtime.active == 0
{%- if auth %}


async def test_a_waiting_poll_holds_no_database_session(
    client: AsyncClient, broker: EventBroker, sessions: list[AsyncSession]
) -> None:
    waiting = asyncio.create_task(
        client.get(POLL, params={"after": broker.start_id, "timeout": 10})
    )
    await asyncio.sleep(0.2)

    assert not waiting.done()
    assert sessions, "authentication should have used a session"
    assert [s for s in sessions if s.in_transaction()] == []

    await create(client)  # releases the poll
    await asyncio.wait_for(waiting, WAIT)
{%- endif %}
