{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
{%- set tenancy = auth and cookiecutter.use_multitenancy == "y" -%}
{%- set cookie_auth = auth and (cookiecutter.use_auth_jwt_cookie == "y" or cookiecutter.use_auth_session == "y") -%}
"""WS /api/v1/ws/items: the item event stream over a WebSocket.

Server to client: one JSON text message per event, ``{"id", "type", "data"}``.
Client to server: the text ``ping`` is answered with ``pong``; anything else is ignored.
Browsers cannot set headers on a WebSocket, so only a cookie method serves a browser;
other clients send their credential headers on the handshake. A credential in the query
string is never accepted (URLs end up in logs).
"""
import asyncio
import contextlib
import logging
{%- if cookie_auth %}
from urllib.parse import urlsplit
{%- endif %}

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.deps import (
    BrokerDep,
    RealtimeDep,
{%- if auth %}
    SessionDep,
{%- endif %}
)
{%- if auth %}
from app.core.auth import authenticate_connection
{%- endif %}
{%- if cookie_auth %}
from app.core.config import get_settings
{%- endif %}
from app.core.exceptions import AppError
from app.realtime.broker import BrokerUnavailable, EventBroker, channel_for
{%- if rbac %}
from app.services.rbac import RbacService
{%- endif %}

logger = logging.getLogger(__name__)

router = APIRouter(tags=["items"])

# RFC 6455 close codes.
GOING_AWAY = 1001
POLICY_VIOLATION = 1008
INTERNAL_ERROR = 1011
TRY_AGAIN_LATER = 1013

# A wait for events is made of slices this long, so a shutdown is noticed within one.
READ_SLICE_SECONDS = 1.0
{%- if cookie_auth %}
COOKIE_METHODS = frozenset({ {%- if cookiecutter.use_auth_jwt_cookie == "y" %}"jwt_cookie"{% endif %}{% if cookiecutter.use_auth_jwt_cookie == "y" and cookiecutter.use_auth_session == "y" %}, {% endif %}{% if cookiecutter.use_auth_session == "y" %}"session"{% endif %}})


def origin_allowed(websocket: WebSocket) -> bool:
    """Whether a browser page may open this WebSocket with its cookies.

    Cookies ride on every WebSocket handshake, from any site, and the browser's
    same-origin policy does not apply to WebSockets: without this check any page could
    read the user's events (cross-site WebSocket hijacking). The Origin header is set by
    the browser and cannot be forged by a page. It must be this server's own origin or
    one of ``cors_origins``. A client that sends no Origin is not a browser, so it has no
    ambient cookies to abuse.
    """
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc == websocket.headers.get("host") or origin in (
        get_settings().cors_origins
    )
{%- endif %}


async def forward(
    websocket: WebSocket, broker: EventBroker, channel: str, cursor: str, closing: asyncio.Event
) -> int:
    """Send events as they are published; returns the close code for the end of the stream."""
    try:
        while not closing.is_set():
            for event in await broker.read(channel, cursor, READ_SLICE_SECONDS):
                cursor = event.id
                await websocket.send_json({"id": event.id, "type": event.type, "data": event.data})
    except BrokerUnavailable as exc:
        # The class only: a message can contain the broker URL and credentials.
        logger.warning("event socket ended: broker unavailable (%s)", type(exc).__name__)
        return INTERNAL_ERROR
    except WebSocketDisconnect:
        return 1000
    return GOING_AWAY


async def answer_pings(websocket: WebSocket) -> None:
    """Read client messages until it disconnects."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        if message.get("text") == "ping":
            await websocket.send_text("pong")


async def relay(
    websocket: WebSocket, broker: EventBroker, channel: str, cursor: str, closing: asyncio.Event
) -> int:
    """Run the event sender and the client reader until either ends; returns the close code."""
    sender = asyncio.create_task(forward(websocket, broker, channel, cursor, closing))
    reader = asyncio.create_task(answer_pings(websocket))
    done, pending = await asyncio.wait({sender, reader}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        task.result()  # an unexpected error is raised here, not swallowed
    return sender.result() if sender in done else 1000


@router.websocket("/ws/items")
async def item_events(
    websocket: WebSocket,
    broker: BrokerDep,
    realtime: RealtimeDep,
{%- if auth %}
    session: SessionDep,
{%- endif %}
) -> None:
    """Item events of the {% if tenancy %}caller's tenant{% else %}app{% endif %}, from the moment of connecting.

    A handshake that fails authentication or authorization is rejected (HTTP 403 on the
    wire, close code 1008 to the application) before the connection is accepted.
    """
{%- if auth %}
    try:
        principal = await authenticate_connection(websocket, session)
        if principal is None:
            raise AppError(401, "not_authenticated", "Not authenticated")
{%- if cookie_auth %}
        if principal.auth_method in COOKIE_METHODS and not origin_allowed(websocket):
            raise AppError(403, "origin_not_allowed", "Origin not allowed")
{%- endif %}
{%- if rbac %}
        if not principal.is_superuser:
            granted = await RbacService(session{% if tenancy %}, principal.tenant_id{% endif %}).permissions_for(
                principal.user_id
            )
            if "items:read" not in granted:
                raise AppError(403, "permission_denied", "Missing required permission: items:read")
{%- endif %}
    except AppError as exc:
        await websocket.close(code=POLICY_VIOLATION, reason=exc.code)
        return
    finally:
        # Authentication is done: release the database connection now, or it would stay
        # pinned for as long as the socket is open.
        await session.close()
    channel = channel_for({% if tenancy %}principal.tenant_id{% else %}None{% endif %})
{%- else %}
    channel = channel_for(None)
{%- endif %}
    try:
        async with realtime.slot():
            cursor = await broker.latest_id(channel) or broker.start_id
            await websocket.accept()
            code = await relay(websocket, broker, channel, cursor, realtime.closing)
    except AppError:
        await websocket.close(code=TRY_AGAIN_LATER, reason="too_many_connections")
        return
    except BrokerUnavailable as exc:
        logger.warning("event socket refused: broker unavailable (%s)", type(exc).__name__)
        await websocket.close(code=INTERNAL_ERROR, reason="events_unavailable")
        return
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await websocket.close(code=code)
