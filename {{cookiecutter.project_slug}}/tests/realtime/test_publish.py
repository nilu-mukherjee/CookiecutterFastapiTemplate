{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set tenancy = auth and cookiecutter.use_multitenancy == "y" -%}
"""ItemService publishes item events after a successful commit, and only then."""
import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import AppError
from app.models import {% if tenancy %}Item, Tenant{% else %}Item{% endif %}
from app.realtime.broker import EventBroker, InMemoryBroker, channel_for
from app.services.item import ItemService


class RecordingBroker(InMemoryBroker):
    """Records every publish and whether the item was committed when it happened."""

    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        super().__init__()
        self.maker = maker
        self.published: list[tuple[str, str, dict[str, Any]]] = []
        self.committed: list[bool] = []

    async def publish(self, channel: str, event_type: str, data: dict[str, Any]) -> str:
        # A separate session sees only committed rows.
        async with self.maker() as session:
            self.committed.append(await session.get(Item, UUID(data["id"])) is not None)
        self.published.append((channel, event_type, data))
        return await super().publish(channel, event_type, data)


class FailingBroker(InMemoryBroker):
    async def publish(self, channel: str, event_type: str, data: dict[str, Any]) -> str:
        raise RuntimeError("redis://user:secret@host is down")


@pytest.fixture
def recorder(maker: async_sessionmaker[AsyncSession]) -> RecordingBroker:
    return RecordingBroker(maker)


@pytest_asyncio.fixture
async def session(maker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with maker() as s:
        yield s
{%- if tenancy %}


def service(session: AsyncSession, broker: EventBroker, tenant: Tenant) -> ItemService:
    return ItemService(session, tenant.id, broker)
{%- else %}


def service(session: AsyncSession, broker: EventBroker) -> ItemService:
    return ItemService(session, broker)
{%- endif %}


async def test_create_update_delete_each_publish_one_id_only_event_after_commit(
    session: AsyncSession, recorder: RecordingBroker{% if tenancy %}, tenant: Tenant{% endif %}
) -> None:
    items = service(session, recorder{% if tenancy %}, tenant{% endif %})
    channel = channel_for({% if tenancy %}tenant.id{% else %}None{% endif %})

    item = await items.create("secret name", "secret description")
    await items.update(item.id, {"name": "other secret"})
    await items.delete(item.id)

    data = {"id": str(item.id)}
    assert recorder.published == [
        (channel, "item.created", data),
        (channel, "item.updated", data),
        (channel, "item.deleted", data),
    ]
    # Committed (visible to another session) when created/updated, gone when deleted.
    assert recorder.committed == [True, True, False]


async def test_failed_commit_publishes_nothing(
    session: AsyncSession,
    recorder: RecordingBroker,
    monkeypatch: pytest.MonkeyPatch,
{%- if tenancy %}
    tenant: Tenant,
{%- endif %}
) -> None:
    async def failing_commit() -> None:
        await session.rollback()
        raise RuntimeError("commit failed")

    monkeypatch.setattr(session, "commit", failing_commit)
    with pytest.raises(RuntimeError):
        await service(session, recorder{% if tenancy %}, tenant{% endif %}).create("x")

    assert recorder.published == []


async def test_missing_item_writes_publish_nothing(
    session: AsyncSession, recorder: RecordingBroker{% if tenancy %}, tenant: Tenant{% endif %}
) -> None:
    items = service(session, recorder{% if tenancy %}, tenant{% endif %})
    with pytest.raises(AppError):
        await items.update(uuid4(), {"name": "x"})
    with pytest.raises(AppError):
        await items.delete(uuid4())

    assert recorder.published == []
{%- if tenancy %}


async def test_other_tenants_item_writes_publish_nothing(
    maker: async_sessionmaker[AsyncSession],
    recorder: RecordingBroker,
    tenant: Tenant,
    tenant_b: Tenant,
) -> None:
    async with maker() as s:
        item = await ItemService(s, tenant.id, InMemoryBroker()).create("a's item")

    async with maker() as s:
        other = ItemService(s, tenant_b.id, recorder)
        with pytest.raises(AppError):
            await other.update(item.id, {"name": "hijacked"})
        with pytest.raises(AppError):
            await other.delete(item.id)

    assert recorder.published == []
{%- endif %}


async def test_failing_publish_does_not_fail_the_write_and_logs_type_only(
    session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
{%- if tenancy %}
    tenant: Tenant,
{%- endif %}
) -> None:
    items = service(session, FailingBroker(){% if tenancy %}, tenant{% endif %})
    with caplog.at_level(logging.WARNING):
        item = await items.create("x")

    assert item.id is not None
    assert "RuntimeError" in caplog.text
    assert "secret" not in caplog.text


# --- through the HTTP API ----------------------------------------------------------------


@pytest.mark.parametrize("version", ["v1", "v2"])
async def test_http_writes_publish(
    application: FastAPI,
    client: AsyncClient,
    version: str,
{%- if tenancy %}
    tenant: Tenant,
{%- endif %}
) -> None:
    broker: EventBroker = application.state.broker
    channel = channel_for({% if tenancy %}tenant.id{% else %}None{% endif %})
    start = broker.start_id

    r = await client.post(f"/api/{version}/items", json={"name": "a"})
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]
    assert (await client.patch(f"/api/{version}/items/{item_id}", json={"name": "b"})).is_success
    assert (await client.delete(f"/api/{version}/items/{item_id}")).status_code == 204
    # A failed write publishes nothing.
    assert (await client.delete(f"/api/{version}/items/{item_id}")).status_code == 404

    events = await broker.read(channel, start, block_seconds=0)
    assert [(e.type, e.data) for e in events] == [
        ("item.created", {"id": item_id}),
        ("item.updated", {"id": item_id}),
        ("item.deleted", {"id": item_id}),
    ]


async def test_http_write_succeeds_when_publishing_fails(
    application: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*args: object, **kwargs: object) -> str:
        raise ConnectionError("broker down")

    monkeypatch.setattr(application.state.broker, "publish", broken)

    r = await client.post("/api/v1/items", json={"name": "kept"})

    assert r.status_code == 201, r.text
    assert (await client.get(f"/api/v1/items/{r.json()['id']}")).status_code == 200
