{%- set redis = cookiecutter.use_redis == "y" -%}
{%- set tenancy = cookiecutter.use_auth == "y" and cookiecutter.use_multitenancy == "y" -%}
"""The EventBroker contract. One suite, run against every implementation in this build."""
import asyncio
import time
from collections.abc import AsyncIterator
{%- if redis %}
from typing import Any
{%- endif %}
{%- if tenancy %}
from uuid import uuid4
{%- endif %}

import pytest
import pytest_asyncio
{%- if redis %}
from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis
{%- endif %}

from app.realtime.broker import (
{%- if redis %}
    BrokerUnavailable,
{%- endif %}
    EventBroker,
    InMemoryBroker,
    InvalidEventId,
    channel_for,
)
{%- if redis %}
from app.realtime.redis_broker import RedisBroker
{%- endif %}

CHANNEL = "items"
MAXLEN = 5
# Generous upper bounds: a blocked read must return well before these.
FAST = 1.0


@pytest_asyncio.fixture(params=["memory"{% if redis %}, "redis"{% endif %}])
async def broker(request: pytest.FixtureRequest) -> AsyncIterator[EventBroker]:
{%- if redis %}
    if request.param == "redis":
        client = FakeRedis(server=FakeServer(), decode_responses=True)
        # Exact trimming here so the retention bound is observable (see the
        # approximate-trimming test below for the production default).
        yield RedisBroker(client, maxlen=MAXLEN, approximate=False)
        await client.aclose()
        return
{%- endif %}
    yield InMemoryBroker(maxlen=MAXLEN)


def order_key(event_id: str) -> tuple[int, ...]:
    """Numeric order of an id of either implementation ("12" or "1700000000000-3")."""
    return tuple(int(part) for part in event_id.split("-"))


def pending_tasks(*, include_fake_server: bool = True) -> list[asyncio.Task[object]]:
    """Tasks other than the test's own.

    fakeredis emulates the Redis SERVER side of a blocking command with a task of its
    own, which (like a real server) lives until the stream changes or the block times
    out; ``include_fake_server=False`` leaves those out to look at client-side tasks only.
    """
    return [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task()
        and (include_fake_server or "FakeSocket" not in t.get_coro().__qualname__)
    ]


async def test_ids_are_strictly_increasing(broker: EventBroker) -> None:
    ids = [await broker.publish(CHANNEL, "item.created", {"n": i}) for i in range(4)]
    assert [order_key(i) for i in ids] == sorted({order_key(i) for i in ids})


async def test_read_returns_events_after_id_in_order(broker: EventBroker) -> None:
    first = await broker.publish(CHANNEL, "item.created", {"n": 1})
    second = await broker.publish(CHANNEL, "item.updated", {"n": 2})
    third = await broker.publish(CHANNEL, "item.deleted", {"n": 3})

    events = await broker.read(CHANNEL, first, block_seconds=0)

    assert [(e.id, e.type, e.data) for e in events] == [
        (second, "item.updated", {"n": 2}),
        (third, "item.deleted", {"n": 3}),
    ]
    assert await broker.read(CHANNEL, third, block_seconds=0) == []


async def test_read_respects_count(broker: EventBroker) -> None:
    start = broker.start_id
    ids = [await broker.publish(CHANNEL, "item.created", {"n": i}) for i in range(4)]

    events = await broker.read(CHANNEL, start, block_seconds=0, count=2)

    assert [e.id for e in events] == ids[:2]


async def test_read_blocks_until_publish_then_returns(broker: EventBroker) -> None:
    start = broker.start_id
    began = time.monotonic()
    reader = asyncio.create_task(broker.read(CHANNEL, start, block_seconds=5))
    await asyncio.sleep(0.05)
    assert not reader.done()

    event_id = await broker.publish(CHANNEL, "item.created", {"id": "x"})
    events = await asyncio.wait_for(reader, FAST)

    assert [(e.id, e.data) for e in events] == [(event_id, {"id": "x"})]
    assert time.monotonic() - began < FAST


async def test_read_times_out_empty(broker: EventBroker) -> None:
    start = broker.start_id
    began = time.monotonic()

    assert await broker.read(CHANNEL, start, block_seconds=0.1) == []
    assert time.monotonic() - began >= 0.09


async def test_after_id_none_ignores_history(broker: EventBroker) -> None:
    await broker.publish(CHANNEL, "item.created", {"n": 1})

    assert await broker.read(CHANNEL, None, block_seconds=0.1) == []


async def test_after_id_none_returns_events_published_after_the_call(
    broker: EventBroker,
) -> None:
    await broker.publish(CHANNEL, "item.created", {"n": 1})
    reader = asyncio.create_task(broker.read(CHANNEL, None, block_seconds=5))
    await asyncio.sleep(0.05)

    later = await broker.publish(CHANNEL, "item.updated", {"n": 2})

    assert [e.id for e in await asyncio.wait_for(reader, FAST)] == [later]


async def test_retention_is_bounded_and_old_cursors_get_the_retained_window(
    broker: EventBroker,
) -> None:
    start = broker.start_id
    ids = [await broker.publish(CHANNEL, "item.created", {"n": i}) for i in range(MAXLEN + 3)]

    # The first three were dropped: a cursor from before them gets what is retained
    # (a gap the client cannot see; the contract documents it).
    events = await broker.read(CHANNEL, start, block_seconds=0)
    assert [e.id for e in events] == ids[-MAXLEN:]
    events = await broker.read(CHANNEL, ids[0], block_seconds=0)
    assert [e.id for e in events] == ids[-MAXLEN:]


async def test_channels_are_isolated(broker: EventBroker) -> None:
    start_b = broker.start_id
    await broker.publish("a:items", "item.created", {"n": 1})

    assert await broker.read("b:items", start_b, block_seconds=0.05) == []


async def test_concurrent_readers_each_get_the_event(broker: EventBroker) -> None:
    start = broker.start_id
    readers = [
        asyncio.create_task(broker.read(CHANNEL, start, block_seconds=5)) for _ in range(5)
    ]
    await asyncio.sleep(0.05)

    event_id = await broker.publish(CHANNEL, "item.created", {"n": 1})
    results = await asyncio.wait_for(asyncio.gather(*readers), FAST)

    assert [[e.id for e in events] for events in results] == [[event_id]] * 5


async def test_cancelled_reader_leaks_nothing(broker: EventBroker) -> None:
    start = broker.start_id
    reader = asyncio.create_task(broker.read(CHANNEL, start, block_seconds=5))
    await asyncio.sleep(0.05)

    reader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reader

    assert pending_tasks(include_fake_server=False) == []
    if isinstance(broker, InMemoryBroker):
        assert broker.waiting(CHANNEL) == 0
    # The broker still works after the cancellation.
    event_id = await broker.publish(CHANNEL, "item.created", {"n": 1})
    assert [e.id for e in await broker.read(CHANNEL, start, block_seconds=0)] == [event_id]
    await asyncio.sleep(0.01)  # the publish released fakeredis's emulated server side
    assert pending_tasks() == []


async def test_latest_id_is_none_when_empty_then_the_newest_id(broker: EventBroker) -> None:
    assert await broker.latest_id(CHANNEL) is None
    first = await broker.publish(CHANNEL, "item.created", {"n": 1})
    assert await broker.latest_id(CHANNEL) == first
    second = await broker.publish(CHANNEL, "item.updated", {"n": 2})
    assert await broker.latest_id(CHANNEL) == second
    # Reading after the newest id waits for new events only.
    assert await broker.read(CHANNEL, second, block_seconds=0) == []


async def test_start_id_precedes_every_event(broker: EventBroker) -> None:
    first = await broker.publish(CHANNEL, "item.created", {"n": 1})
    assert [e.id for e in await broker.read(CHANNEL, broker.start_id, block_seconds=0)] == [first]


async def test_event_data_round_trips(broker: EventBroker) -> None:
    start = broker.start_id
    data = {"id": "7f9c", "nested": {"list": [1, 2.5, None, True], "text": "é"}}
    await broker.publish(CHANNEL, "item.created", data)

    (event,) = await broker.read(CHANNEL, start, block_seconds=0)

    assert event.data == data


@pytest.mark.parametrize("bad", ["abc", "-1", "1.5", "", " 1", "1-0-0", "0x10", "1" * 40])
async def test_invalid_after_id_raises(broker: EventBroker, bad: str) -> None:
    with pytest.raises(InvalidEventId):
        await broker.read(CHANNEL, bad, block_seconds=0)


async def test_in_memory_ids_are_decimal_counters() -> None:
    broker = InMemoryBroker()
    assert [await broker.publish(CHANNEL, "t", {}) for _ in range(3)] == ["1", "2", "3"]
    # Counters are per channel.
    assert await broker.publish("other", "t", {}) == "1"
    with pytest.raises(InvalidEventId):
        await broker.read(CHANNEL, "1-0", block_seconds=0)
    with pytest.raises(InvalidEventId):
        await broker.read(CHANNEL, "²", block_seconds=0)  # isdigit() would accept it


async def test_in_memory_drops_channels_nobody_uses() -> None:
    broker = InMemoryBroker()
    # A read of a channel that never had events must not keep it alive.
    assert await broker.read("ghost", None, block_seconds=0.01) == []
    assert await broker.read("ghost", "0", block_seconds=0) == []
    assert await broker.latest_id("ghost") is None
    assert broker.channel_count() == 0


async def test_in_memory_waiting_counts_blocked_readers() -> None:
    broker = InMemoryBroker()
    readers = [asyncio.create_task(broker.read(CHANNEL, None, block_seconds=5)) for _ in range(3)]
    await asyncio.sleep(0.01)
    assert broker.waiting(CHANNEL) == 3

    await broker.publish(CHANNEL, "t", {})
    await asyncio.wait_for(asyncio.gather(*readers), FAST)

    assert broker.waiting(CHANNEL) == 0
def test_channel_for_none_is_the_items_channel() -> None:
    assert channel_for(None) == "items"
{%- if tenancy %}


def test_channel_is_per_tenant() -> None:
    tenant_id = uuid4()
    assert channel_for(tenant_id) == f"tenant:{tenant_id}:items"
    assert channel_for(uuid4()) != channel_for(tenant_id)
    assert channel_for(tenant_id) != channel_for(None)
{%- endif %}
{%- if redis %}


# --- Redis Streams specifics -----------------------------------------------------------


def redis_broker(server: FakeServer | None = None, **kwargs: Any) -> RedisBroker:
    client = FakeRedis(server=server or FakeServer(), decode_responses=True)
    return RedisBroker(client, **kwargs)


@pytest.mark.parametrize("bad", ["1", "-1-0", "1-", "-1", "1-0-0", "a-b", "$", ">", "+", "-"])
async def test_redis_rejects_ids_that_are_not_stream_ids(bad: str) -> None:
    broker = redis_broker()
    with pytest.raises(InvalidEventId):
        await broker.read(CHANNEL, bad, block_seconds=0)
    await broker.client.aclose()


async def test_redis_ids_are_stream_ids_in_a_prefixed_stream() -> None:
    broker = redis_broker()
    event_id = await broker.publish(CHANNEL, "item.created", {"id": "x"})

    assert order_key(event_id) > (0, 0) and event_id.count("-") == 1
    entries = await broker.client.xrange(f"events:{CHANNEL}")
    assert entries == [(event_id, {"type": "item.created", "data": '{"id": "x"}'})]
    await broker.client.aclose()


async def test_redis_trims_approximately_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    broker = redis_broker(maxlen=123)
    calls: list[dict[str, Any]] = []
    original = broker.client.xadd

    async def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return await original(*args, **kwargs)

    monkeypatch.setattr(broker.client, "xadd", spy)
    await broker.publish(CHANNEL, "item.created", {})

    assert calls == [{"maxlen": 123, "approximate": True}]
    await broker.client.aclose()


async def test_redis_reads_use_the_blocking_client(monkeypatch: pytest.MonkeyPatch) -> None:
    # Blocking XREADs outlive the command client's short socket timeout, so they go
    # through their own client.
    server = FakeServer()
    client = FakeRedis(server=server, decode_responses=True)
    blocking = FakeRedis(server=server, decode_responses=True)
    broker = RedisBroker(client, blocking_client=blocking)
    used: list[str] = []
    for name, target in (("client", client), ("blocking", blocking)):

        async def spy(*args: Any, _name: str = name, _orig: Any = target.xread, **kw: Any) -> Any:
            used.append(_name)
            return await _orig(*args, **kw)

        monkeypatch.setattr(target, "xread", spy)

    await broker.read(CHANNEL, broker.start_id, block_seconds=0.01)

    assert used == ["blocking"]
    await client.aclose()
    await blocking.aclose()


async def test_redis_zero_block_does_not_block_forever() -> None:
    # XREAD BLOCK 0 means "forever" in Redis; block_seconds=0 must mean "do not wait".
    broker = redis_broker()
    start = broker.start_id
    assert await asyncio.wait_for(broker.read(CHANNEL, start, block_seconds=0), FAST) == []
    assert await asyncio.wait_for(broker.read(CHANNEL, None, block_seconds=0), FAST) == []
    await broker.client.aclose()


@pytest.mark.parametrize("operation", ["read", "latest_id", "publish"])
async def test_redis_outage_raises_broker_unavailable(operation: str) -> None:
    server = FakeServer()
    broker = redis_broker(server)
    server.connected = False
    with pytest.raises(BrokerUnavailable):
        if operation == "read":
            await broker.read(CHANNEL, "0-0", block_seconds=0)
        elif operation == "latest_id":
            await broker.latest_id(CHANNEL)
        else:
            await broker.publish(CHANNEL, "item.created", {})
    await broker.client.aclose()
{%- endif %}
