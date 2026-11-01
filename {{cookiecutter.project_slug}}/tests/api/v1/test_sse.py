{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
{%- set tenancy = auth and cookiecutter.use_multitenancy == "y" -%}
{%- set redis = cookiecutter.use_redis == "y" -%}
"""GET /api/v1/items/events: the item event stream (Server-Sent Events)."""
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.core.config import get_settings
{%- if tenancy %}
from app.models import Tenant
{%- endif %}
from app.realtime.broker import BrokerUnavailable, EventBroker, channel_for
from tests.api.v1.streaming import Stream
{%- if rbac or tenancy %}
from tests.conftest import {% if rbac %}LoginClient, {% endif %}MakeUser
{%- endif %}
{%- if tenancy %}
from tests.tenancy import conftest as tenancy_clients
from tests.tenancy.conftest import METHODS, MethodClient
{%- endif %}

EVENTS = "/api/v1/items/events"
ITEMS = "/api/v1/items"
QUIET = 0.3  # how long "nothing arrives" is observed for

{%- if tenancy %}


# The tenancy tests' per-auth-method clients, reused here (fixtures are per directory).
method_client = tenancy_clients.method_client
{%- endif %}


@pytest.fixture
def fast_heartbeat(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SSE_HEARTBEAT_SECONDS", "0.05")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


def parse(frame: str | None) -> dict[str, str]:
    assert frame is not None
    return dict(line.split(": ", 1) for line in frame.strip("\n").split("\n"))


async def create(client: AsyncClient, name: str = "a") -> str:
    r = await client.post(ITEMS, json={"name": name})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def test_stream_delivers_a_created_event(application: FastAPI, client: AsyncClient) -> None:
    async with Stream(application, client, EVENTS) as stream:
        assert stream.status == 200
        item_id = await create(client)

        fields = parse(await stream.next_frame())

    assert set(fields) == {"id", "event", "data"}
    assert fields["event"] == "item.created"
    assert json.loads(fields["data"]) == {"id": item_id}
    assert fields["id"]


async def test_stream_headers(application: FastAPI, client: AsyncClient) -> None:
    async with Stream(application, client, EVENTS) as stream:
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert stream.headers["cache-control"] == "no-store"
        assert stream.headers["x-accel-buffering"] == "no"


async def test_stream_delivers_updates_and_deletes_in_order(
    application: FastAPI, client: AsyncClient
) -> None:
    async with Stream(application, client, EVENTS) as stream:
        item_id = await create(client)
        await client.patch(f"{ITEMS}/{item_id}", json={"name": "b"})
        await client.delete(f"{ITEMS}/{item_id}")

        frames = [parse(await stream.next_frame()) for _ in range(3)]

    assert [f["event"] for f in frames] == ["item.created", "item.updated", "item.deleted"]
    assert len({f["id"] for f in frames}) == 3


async def test_stream_without_last_event_id_starts_from_now(
    application: FastAPI, client: AsyncClient
) -> None:
    await create(client, "before")
    async with Stream(application, client, EVENTS) as stream:
        with pytest.raises(TimeoutError):
            await stream.next_frame(QUIET)


async def test_last_event_id_resumes_after_that_event(
    application: FastAPI,
    client: AsyncClient,
    broker: EventBroker,
    channel: str,
) -> None:
    first = await create(client, "one")
    second = await create(client, "two")
    third = await create(client, "three")
    first_event = (await broker.read(channel, broker.start_id, 0))[0]

    async with Stream(
        application, client, EVENTS, headers={"Last-Event-ID": first_event.id}
    ) as stream:
        frames = [parse(await stream.next_frame()) for _ in range(2)]
        with pytest.raises(TimeoutError):
            await stream.next_frame(QUIET)

    assert [json.loads(f["data"])["id"] for f in frames] == [second, third]
    assert first not in [json.loads(f["data"])["id"] for f in frames]


@pytest.mark.parametrize("bad", ["abc", "-1", "1.5", "1-0-0", "x" * 100, "0x10"])
async def test_invalid_last_event_id_is_400_before_streaming(
    application: FastAPI, client: AsyncClient, bad: str
) -> None:
    async with Stream(application, client, EVENTS, headers={"Last-Event-ID": bad}) as stream:
        assert stream.status == 400
        assert not stream.headers["content-type"].startswith("text/event-stream")
        body = json.loads(await stream.body())

    assert body["code"] == "invalid_event_id"


@pytest.mark.usefixtures("fast_heartbeat")
async def test_idle_stream_sends_heartbeat_comments(
    application: FastAPI, client: AsyncClient
) -> None:
    async with Stream(application, client, EVENTS) as stream:
        assert await stream.next_frame() == ": keepalive\n\n"
        assert await stream.next_frame() == ": keepalive\n\n"


@pytest.mark.usefixtures("fast_heartbeat")
async def test_events_still_arrive_between_heartbeats(
    application: FastAPI, client: AsyncClient
) -> None:
    async with Stream(application, client, EVENTS) as stream:
        assert await stream.next_frame() == ": keepalive\n\n"
        item_id = await create(client)
        frames = [await stream.next_frame() for _ in range(2)]

    events = [parse(f) for f in frames if f is not None and not f.startswith(":")]
    assert [json.loads(e["data"])["id"] for e in events] == [item_id]


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_stream_ends_cleanly_on_disconnect(
    application: FastAPI,
    client: AsyncClient,
    broker: EventBroker,
    channel: str,
    spec_version: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("SSE_HEARTBEAT_SECONDS", "0.05")
    get_settings.cache_clear()
    try:
        with caplog.at_level(logging.WARNING):
            stream = Stream(application, client, EVENTS, spec_version=spec_version)
            async with stream:
                await create(client)
                assert parse(await stream.next_frame())["event"] == "item.created"
            # __aexit__ disconnected and waited: the app finished without an error.
            assert stream.task is not None and stream.task.done()
    finally:
        get_settings.cache_clear()

    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    waiting = getattr(broker, "waiting", None)
    if waiting is not None:
        assert waiting(channel) == 0


async def test_broker_outage_ends_the_stream_cleanly(
    application: FastAPI,
    client: AsyncClient,
    broker: EventBroker,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("SSE_HEARTBEAT_SECONDS", "0.05")
    get_settings.cache_clear()
    original = broker.read
    calls = 0

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls > 1:  # the first read is the one made before streaming starts
            raise BrokerUnavailable("ConnectionError")
        return await original(*args, **kwargs)

    monkeypatch.setattr(broker, "read", flaky)
    try:
        with caplog.at_level(logging.WARNING):
            async with Stream(application, client, EVENTS) as stream:
                while await stream.next_frame() is not None:
                    pass  # heartbeats, then the end of the stream
                await stream.ended()
    finally:
        get_settings.cache_clear()

    assert stream.status == 200
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert all(r.exc_info is None for r in caplog.records)
{%- if redis %}


async def test_redis_outage_ends_the_stream_cleanly(
    application: FastAPI,
    client: AsyncClient,
    fake_redis: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SSE_HEARTBEAT_SECONDS", "0.05")
    get_settings.cache_clear()
    try:
        async with Stream(application, client, EVENTS) as stream:
            fake_redis.connected = False
            while await stream.next_frame() is not None:
                pass
            await stream.ended()
    finally:
        get_settings.cache_clear()
{%- endif %}


async def test_events_route_is_not_swallowed_by_the_item_route(
    application: FastAPI, client: AsyncClient
) -> None:
    # Registered after /items/{item_id}, "events" would be parsed as an item id (422).
    async with Stream(application, client, EVENTS) as stream:
        assert stream.status == 200
        assert stream.headers["content-type"].startswith("text/event-stream")


async def test_v2_has_no_event_stream(client: AsyncClient) -> None:
    r = await client.get("/api/v2/items/events")
    assert not r.headers.get("content-type", "").startswith("text/event-stream")
    assert r.status_code in (404, 422)
{%- if auth %}


async def test_unauthenticated_is_401(application: FastAPI, anon_client: AsyncClient) -> None:
    async with Stream(application, anon_client, EVENTS) as stream:
        assert stream.status == 401
        assert json.loads(await stream.body())["code"]
{%- endif %}
{%- if rbac %}


async def test_without_items_read_is_403(
    application: FastAPI, make_user: MakeUser, login_client: LoginClient
) -> None:
    nobody = await login_client(await make_user("nobody@example.com", roles=[]))
    async with Stream(application, nobody, EVENTS) as stream:
        assert stream.status == 403
        body = json.loads(await stream.body())

    assert body["code"] == "permission_denied"
    assert "items:read" in body["detail"]

{%- endif %}
{%- if tenancy %}


async def test_tenants_streams_are_isolated(
    application: FastAPI, client: AsyncClient, client_b: AsyncClient
) -> None:
    async with (
        Stream(application, client, EVENTS) as stream_a,
        Stream(application, client_b, EVENTS) as stream_b,
    ):
        a_item = await create(client, "a-secret")

        assert json.loads(parse(await stream_a.next_frame())["data"]) == {"id": a_item}
        with pytest.raises(TimeoutError):
            await stream_b.next_frame(QUIET)

        b_item = await create(client_b, "b-secret")

        assert json.loads(parse(await stream_b.next_frame())["data"]) == {"id": b_item}
        with pytest.raises(TimeoutError):
            await stream_a.next_frame(QUIET)


async def test_another_tenants_last_event_id_gets_nothing_of_that_tenant(
    application: FastAPI,
    client: AsyncClient,
    client_b: AsyncClient,
    broker: EventBroker,
    tenant: Tenant,
) -> None:
    await create(client, "a-one")
    await create(client, "a-two")
    a_first = (await broker.read(channel_for(tenant.id), broker.start_id, 0))[0]

    # B replays "from A's first event": B's own channel is empty, so nothing arrives
    # (ids are per channel: an id from another tenant is just a position, never data).
    async with Stream(
        application, client_b, EVENTS, headers={"Last-Event-ID": a_first.id}
    ) as stream:
        with pytest.raises(TimeoutError):
            await stream.next_frame(QUIET)

    b_item = await create(client_b, "b-one")
    # From B's own start position, B gets B's event and still nothing of A's.
    async with Stream(
        application, client_b, EVENTS, headers={"Last-Event-ID": broker.start_id}
    ) as replay:
        assert json.loads(parse(await replay.next_frame())["data"]) == {"id": b_item}
        with pytest.raises(TimeoutError):
            await replay.next_frame(QUIET)


async def test_system_tenant_superuser_does_not_see_tenant_events(
    application: FastAPI,
    client: AsyncClient,
    make_user: MakeUser,
    method_client: MethodClient,
    system_tenant: Tenant,
) -> None:
    root = await method_client(
        METHODS[0],
        await make_user("root@example.com", superuser=True, tenant=system_tenant)
    )
    async with Stream(application, root, EVENTS) as stream:
        await create(client, "tenant-data")
        with pytest.raises(TimeoutError):
            await stream.next_frame(QUIET)
{%- endif %}
