{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
{%- set tenancy = auth and cookiecutter.use_multitenancy == "y" -%}
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from kombu.exceptions import OperationalError
{% if cookiecutter.use_redis == "y" %}from redis.exceptions import ConnectionError as RedisConnectionError
{% endif %}
{% if tenancy %}from app.models import Tenant
{% endif %}from app.worker.tasks.example import example_task
{% if rbac %}from tests.conftest import LoginClient, MakeUser
{% endif %}
Calls = list[dict[str, Any]]


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Calls:
    """Records every publish of the example task, then runs it eagerly."""
    recorded: Calls = []
    original = example_task.apply_async

    def spy(*args: Any, **kwargs: Any) -> Any:
        recorded.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(example_task, "apply_async", spy)
    return recorded


async def make_item(client: AsyncClient) -> str:
    r = await client.post("/api/v1/items", json={"name": "to process", "description": "secret"})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def test_process_endpoint_enqueues{% if tenancy %}_with_tenant_id{% endif %}(
    client: AsyncClient, calls: Calls{% if tenancy %}, tenant: Tenant{% endif %}
) -> None:
    item_id = await make_item(client)
    r = await client.post(f"/api/v1/items/{item_id}/process")
    assert r.status_code == 202, r.text
    assert set(r.json()) == {"task_id"}
    assert str(UUID(r.json()["task_id"])) == r.json()["task_id"]
    assert len(calls) == 1
    # Publishing must fail fast when the broker is down, not retry for a request's lifetime.
    assert calls[0]["retry"] is False
    # The payload names the item; it never carries its contents.
    assert calls[0]["kwargs"] == {
{%- if tenancy %}
        "tenant_id": str(tenant.id),
{%- endif %}
        "payload": {"item_id": item_id},
    }


async def test_process_missing_item_is_404_and_nothing_is_enqueued(
    client: AsyncClient, calls: Calls
) -> None:
    r = await client.post(f"/api/v1/items/{uuid4()}/process")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"
    assert calls == []
{%- if tenancy %}


async def test_process_other_tenants_item_is_404_and_nothing_is_enqueued(
    client: AsyncClient, client_b: AsyncClient, calls: Calls
) -> None:
    other = await make_item(client_b)
    r = await client.post(f"/api/v1/items/{other}/process")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"
    assert calls == []
{%- endif %}
{%- if auth %}


async def test_process_requires_authentication(
    anon_client: AsyncClient, calls: Calls
) -> None:
    r = await anon_client.post(f"/api/v1/items/{uuid4()}/process")
    assert r.status_code == 401
    assert calls == []
{%- endif %}
{%- if rbac %}


async def test_process_requires_items_write(
    make_user: MakeUser, login_client: LoginClient, client: AsyncClient, calls: Calls
) -> None:
    item_id = await make_item(client)
    nobody = await login_client(await make_user("nobody@example.com", roles=[]))
    r = await nobody.post(f"/api/v1/items/{item_id}/process")
    assert r.status_code == 403
    assert r.json()["code"] == "permission_denied"
    assert calls == []
{%- endif %}


async def test_broker_down_is_503_without_leaking_the_broker(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    item_id = await make_item(client)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise OperationalError("amqp://guest:secret-pw@rabbit.internal:5672// refused")

    monkeypatch.setattr(example_task, "apply_async", broken)
    r = await client.post(f"/api/v1/items/{item_id}/process")
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "queue_unavailable"
    assert "secret-pw" not in r.text
    assert "amqp" not in r.text
    assert "rabbit" not in r.text


async def test_broker_oserror_is_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    item_id = await make_item(client)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(example_task, "apply_async", broken)
    r = await client.post(f"/api/v1/items/{item_id}/process")
    assert r.status_code == 503
    assert r.json()["code"] == "queue_unavailable"
{%- if cookiecutter.use_redis == "y" %}


async def test_redis_outage_on_enqueue_is_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The result backend talks to Redis while publishing; RedisError is not an OSError.
    item_id = await make_item(client)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RedisConnectionError("redis://:secret-pw@redis.internal:6379/1 boom")

    monkeypatch.setattr(example_task, "apply_async", broken)
    r = await client.post(f"/api/v1/items/{item_id}/process")
    assert r.status_code == 503
    assert r.json()["code"] == "queue_unavailable"
    assert r.json()["detail"] == "Task queue is unavailable"
    assert "secret-pw" not in r.text
    assert "redis" not in r.text.lower()
{%- endif %}
