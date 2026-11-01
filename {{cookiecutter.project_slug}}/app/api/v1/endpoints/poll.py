{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, {% if auth %}Depends, {% endif %}Query, Response

from app.api.deps import (
    BrokerDep,
    ChannelDep,
    RealtimeDep,
{%- if auth %}
    SessionDep,
    get_current_principal,
{%- endif %}
{%- if rbac %}
    require_permission,
{%- endif %}
)
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.realtime.broker import BrokerUnavailable, Event, EventBroker, InvalidEventId
from app.schemas.v1.event import EventBatch, EventRead

logger = logging.getLogger(__name__)

# A long wait is made of slices this long, so a shutdown is noticed within one slice.
WAIT_SLICE_SECONDS = 1.0


async def wait_for_events(
    broker: EventBroker, channel: str, after: str, seconds: float, closing: asyncio.Event
) -> list[Event]:
    """Events after ``after``, waiting up to ``seconds``; [] on timeout or shutdown."""
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if closing.is_set():
            return await broker.read(channel, after, 0)
        events = await broker.read(channel, after, max(0.0, min(remaining, WAIT_SLICE_SECONDS)))
        if events or remaining <= WAIT_SLICE_SECONDS:
            return events

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

@router.get(
    "/poll",
    response_model=EventBatch,
    responses={204: {"description": "No event arrived before the timeout."}}{% if rbac %},
    dependencies=READ{% endif %},
)
async def poll_events(
    broker: BrokerDep,
    channel: ChannelDep,
    realtime: RealtimeDep,
{%- if auth %}
    session: SessionDep,
{%- endif %}
    after: Annotated[str | None, Query(description="Id of the last event already seen.")] = None,
    timeout: Annotated[
        float,
        Query(ge=0, allow_inf_nan=False, description="Seconds to wait for an event."),
    ] = 25.0,
) -> EventBatch | Response:
    """Long polling of this {% if cookiecutter.use_multitenancy == "y" and auth %}tenant's {% endif %}item events.

    Without ``after``: answers at once with ``last_id`` (the newest event id, or null) to
    start polling from. With it: answers at once when events exist after that id,
    otherwise waits up to ``timeout`` seconds (at most ``long_poll_max_seconds``) and
    answers 204 when nothing arrived.
    """
{%- if auth %}
    # Authentication and authorization are done: release the request's database
    # connection now, or it would stay pinned for the whole wait.
    await session.close()
{%- endif %}
    try:
        if after is None:
            return EventBatch(events=[], last_id=await broker.latest_id(channel))
        wait = min(timeout, get_settings().long_poll_max_seconds)
        async with realtime.slot():
            events = await wait_for_events(broker, channel, after, wait, realtime.closing)
    except InvalidEventId as exc:
        raise AppError(400, "invalid_event_id", "Invalid after") from exc
    except BrokerUnavailable as exc:
        # The class only: a message can contain the broker URL and credentials.
        logger.warning("event poll failed: broker unavailable (%s)", type(exc).__name__)
        raise AppError(503, "events_unavailable", "Events are unavailable") from exc
    if not events:
        return Response(status_code=204)
    return EventBatch(
        events=[EventRead(id=e.id, type=e.type, data=e.data) for e in events],
        last_id=events[-1].id,
    )
