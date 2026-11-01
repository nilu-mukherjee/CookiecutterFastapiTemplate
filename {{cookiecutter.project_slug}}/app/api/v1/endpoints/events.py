{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, {% if auth %}Depends, {% endif %}Header, Request
from fastapi.responses import StreamingResponse

from app.api.deps import BrokerDep, ChannelDep{% if auth %}, get_current_principal{% endif %}{% if rbac %}, require_permission{% endif %}
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.realtime.broker import BrokerUnavailable, Event, EventBroker, InvalidEventId

logger = logging.getLogger(__name__)

{% if auth %}router = APIRouter(
    prefix="/items",
    tags=["items"],
    dependencies=[Depends(get_current_principal)],
)
{% else %}router = APIRouter(prefix="/items", tags=["items"])
{% endif %}{% if rbac %}
# Checked after authentication, so an unauthenticated request is a 401, not a 403.
READ = [Depends(require_permission("items:read"))]
{% endif %}

def frame(event: Event) -> str:
    """One Server-Sent Events message; ``id`` lets the browser resume with Last-Event-ID."""
    return f"id: {event.id}\nevent: {event.type}\ndata: {json.dumps(event.data)}\n\n"


async def event_frames(
    request: Request,
    broker: EventBroker,
    channel: str,
    cursor: str,
    first: list[Event],
    heartbeat_seconds: float,
) -> AsyncIterator[str]:
    """The stream: events as they are published, a comment while idle.

    Ends when the client disconnects, or (logged, no traceback to the client) when the
    broker becomes unavailable; the browser's EventSource then reconnects with
    Last-Event-ID and resumes.
    """
    pending = first
    while True:
        for event in pending:
            cursor = event.id
            yield frame(event)
        if await request.is_disconnected():
            return
        try:
            pending = await broker.read(channel, cursor, heartbeat_seconds)
        except BrokerUnavailable as exc:
            # The class only: a message can contain the broker URL and credentials.
            logger.warning("event stream ended: broker unavailable (%s)", type(exc).__name__)
            return
        if not pending:
            yield ": keepalive\n\n"


@router.get(
    "/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}}{% if rbac %},
    dependencies=READ{% endif %},
)
async def stream_events(
    request: Request,
    broker: BrokerDep,
    channel: ChannelDep,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    """Server-Sent Events of the {% if cookiecutter.use_multitenancy == "y" and auth %}caller's tenant's {% endif %}item changes.

    Events are ``item.created``, ``item.updated`` and ``item.deleted``, each with the
    item's ``{"id": ...}`` only.

    Without ``Last-Event-ID`` the stream starts from now; with it, from the event after it.
    """
    try:
        if last_event_id is None:
            # Resolve "now" to a position here, so no event published between the
            # reads below can be missed.
            cursor = await broker.latest_id(channel) or broker.start_id
            first: list[Event] = []
        else:
            cursor = last_event_id
            # Also validates the id: a bad one is a 400 before any streaming starts.
            first = await broker.read(channel, last_event_id, 0)
    except InvalidEventId as exc:
        raise AppError(400, "invalid_event_id", "Invalid Last-Event-ID") from exc
    except BrokerUnavailable as exc:
        logger.warning("event stream refused: broker unavailable (%s)", type(exc).__name__)
        raise AppError(503, "events_unavailable", "Events are unavailable") from exc
    return StreamingResponse(
        event_frames(
            request, broker, channel, cursor, first, get_settings().sse_heartbeat_seconds
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
