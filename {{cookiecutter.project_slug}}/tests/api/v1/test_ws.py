{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set bearer = auth and cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set cookie = auth and cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set session = auth and cookiecutter.use_auth_session == "y" -%}
{%- set cookie_auth = cookie or session -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
{%- set tenancy = auth and cookiecutter.use_multitenancy == "y" -%}
"""WS /api/v1/ws/items: the item event stream over a WebSocket."""
import logging
{%- if bearer %}
from collections.abc import Callable
{%- endif %}
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
{% if cookie_auth %}
from app.core.config import get_settings
{%- endif %}
{%- if bearer %}
from app.models import User
{%- endif %}
from app.realtime.broker import BrokerUnavailable, EventBroker
from app.realtime.state import RealtimeState
from tests.api.v1.ws_client import Socket
{%- if bearer or cookie_auth or rbac %}
from tests.conftest import {% if rbac %}LoginClient, {% endif %}{% if cookie %}MakeCookieClient, {% elif session %}MakeSessionClient, {% endif %}MakeUser
{%- endif %}

WS = "/api/v1/ws/items"
ITEMS = "/api/v1/items"
QUIET = 0.3  # how long "nothing arrives" is observed for


@pytest.fixture
def broker(application: FastAPI) -> EventBroker:
    broker: EventBroker = application.state.broker
    return broker


async def create(client: AsyncClient, name: str = "a") -> str:
    r = await client.post(ITEMS, json={"name": name})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def test_ws_receives_item_created_event(application: FastAPI, client: AsyncClient) -> None:
    async with Socket(application, client, WS) as ws:
        assert ws.accepted
        item_id = await create(client)

        event = await ws.receive_json()

    assert set(event) == {"id", "type", "data"}
    assert event["type"] == "item.created"
    assert event["data"] == {"id": item_id}


async def test_ws_delivers_updates_and_deletes_in_order(
    application: FastAPI, client: AsyncClient
) -> None:
    async with Socket(application, client, WS) as ws:
        item_id = await create(client)
        await client.patch(f"{ITEMS}/{item_id}", json={"name": "b"})
        await client.delete(f"{ITEMS}/{item_id}")

        events = [await ws.receive_json() for _ in range(3)]

    assert [e["type"] for e in events] == ["item.created", "item.updated", "item.deleted"]
    assert len({e["id"] for e in events}) == 3


async def test_ws_starts_from_now(application: FastAPI, client: AsyncClient) -> None:
    await create(client, "before")
    async with Socket(application, client, WS) as ws:
        with pytest.raises(TimeoutError):
            await ws.receive_text(QUIET)


async def test_ws_answers_ping_with_pong(application: FastAPI, client: AsyncClient) -> None:
    async with Socket(application, client, WS) as ws:
        await ws.send_text("ping")
        assert await ws.receive_text() == "pong"
        # Other messages are ignored and do not end the socket.
        await ws.send_text("hello")
        await ws.send_bytes(b"\x00")
        await ws.send_text("ping")
        assert await ws.receive_text() == "pong"


async def test_ws_disconnect_releases_the_slot(
    application: FastAPI, client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        async with Socket(application, client, WS) as ws:
            assert ws.accepted
            assert application.state.realtime.active == 1
    assert application.state.realtime.active == 0
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


async def test_ws_too_many_connections_closed_1013_and_slots_are_released(
    application: FastAPI, client: AsyncClient
) -> None:
    application.state.realtime = RealtimeState(1)

    async with Socket(application, client, WS) as first:
        assert first.accepted
        async with Socket(application, client, WS) as second:
            assert not second.accepted
            assert second.close_code == 1013

    async with Socket(application, client, WS) as again:
        assert again.accepted


async def test_ws_closing_ends_an_open_socket(application: FastAPI, client: AsyncClient) -> None:
    async with Socket(application, client, WS) as ws:
        application.state.realtime.closing.set()

        assert await ws.receive_text(3) is None
        await ws.ended()

    assert ws.close_code == 1001


async def test_ws_broker_outage_closes_with_1011(
    application: FastAPI,
    client: AsyncClient,
    broker: EventBroker,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    original = broker.read
    calls = 0

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls > 0:
            raise BrokerUnavailable("ConnectionError")
        return await original(*args, **kwargs)

    monkeypatch.setattr(broker, "read", flaky)
    with caplog.at_level(logging.WARNING):
        async with Socket(application, client, WS) as ws:
            assert await ws.receive_text() is None
            await ws.ended()

    assert ws.close_code == 1011
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert all(r.exc_info is None for r in caplog.records)


async def test_v2_has_no_websocket(application: FastAPI, client: AsyncClient) -> None:
    async with Socket(application, client, "/api/v2/ws/items") as ws:
        assert not ws.accepted
{%- if auth %}


async def test_ws_without_credentials_closed_1008(
    application: FastAPI, anon_client: AsyncClient
) -> None:
    async with Socket(application, anon_client, WS) as ws:
        assert not ws.accepted
        assert ws.close_code == 1008
{%- endif %}
{%- if bearer %}


async def test_ws_bearer_header_handshake_ok(
    application: FastAPI,
    anon_client: AsyncClient,
    make_user: MakeUser,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    user = await make_user("socket@example.com")
    async with Socket(application, anon_client, WS, headers=auth_headers(user)) as ws:
        assert ws.accepted


async def test_ws_invalid_bearer_closed_1008(
    application: FastAPI, anon_client: AsyncClient
) -> None:
    headers = {"Authorization": "Bearer not-a-token"}
    async with Socket(application, anon_client, WS, headers=headers) as ws:
        assert ws.close_code == 1008


async def test_ws_query_string_token_not_accepted(
    application: FastAPI,
    anon_client: AsyncClient,
    make_user: MakeUser,
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    user = await make_user("socket@example.com")
    token = auth_headers(user)["Authorization"].split()[1]
    async with Socket(application, anon_client, f"{WS}?token={token}&access_token={token}") as ws:
        assert not ws.accepted
        assert ws.close_code == 1008
{%- endif %}
{%- if cookie_auth %}


@pytest.fixture
def make_browser(
{%- if cookie %}
    make_cookie_client: MakeCookieClient,
{%- else %}
    make_session_client: MakeSessionClient,
{%- endif %}
    make_user: MakeUser,
) -> Any:
    """A client logged in with a cookie method, as a browser would be."""

    async def _make() -> AsyncClient:
        user = await make_user("browser@example.com")
        return await {% if cookie %}make_cookie_client{% else %}make_session_client{% endif %}(user.email)

    return _make


async def test_ws_cookie_handshake_with_same_origin_ok(
    application: FastAPI, make_browser: Any
) -> None:
    browser = await make_browser()
    async with Socket(application, browser, WS, headers={"Origin": "http://test"}) as ws:
        assert ws.accepted


async def test_ws_cookie_handshake_without_origin_ok(
    application: FastAPI, make_browser: Any
) -> None:
    # Not a browser (browsers always send Origin): no ambient cookies to abuse.
    browser = await make_browser()
    async with Socket(application, browser, WS) as ws:
        assert ws.accepted


async def test_ws_cookie_handshake_with_foreign_origin_closed_1008(
    application: FastAPI, make_browser: Any
) -> None:
    browser = await make_browser()
    async with Socket(application, browser, WS, headers={"Origin": "https://evil.example"}) as ws:
        assert not ws.accepted
        assert ws.close_code == 1008


async def test_ws_cookie_handshake_from_a_cors_origin_ok(
    application: FastAPI, make_browser: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", '["https://app.example"]')
    get_settings.cache_clear()
    try:
        browser = await make_browser()
        async with Socket(
            application, browser, WS, headers={"Origin": "https://app.example"}
        ) as ws:
            assert ws.accepted
    finally:
        get_settings.cache_clear()
{%- endif %}
{%- if rbac %}


async def test_ws_without_items_read_closed_1008(
    application: FastAPI, make_user: MakeUser, login_client: LoginClient
) -> None:
    nobody = await login_client(await make_user("nobody@example.com", roles=[]))
    async with Socket(application, nobody, WS) as ws:
        assert not ws.accepted
        assert ws.close_code == 1008
{%- endif %}
{%- if tenancy %}


async def test_ws_never_receives_other_tenant_events(
    application: FastAPI, client: AsyncClient, client_b: AsyncClient
) -> None:
    async with (
        Socket(application, client, WS) as ws_a,
        Socket(application, client_b, WS) as ws_b,
    ):
        a_item = await create(client, "a-secret")

        assert (await ws_a.receive_json())["data"] == {"id": a_item}
        with pytest.raises(TimeoutError):
            await ws_b.receive_text(QUIET)

        b_item = await create(client_b, "b-secret")

        assert (await ws_b.receive_json())["data"] == {"id": b_item}
        with pytest.raises(TimeoutError):
            await ws_a.receive_text(QUIET)
{%- endif %}
