{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
import logging
from typing import Annotated

from fastapi import APIRouter, {% if auth %}Depends, {% endif %}Query, Response

from app.api.deps import BrokerDep, ChannelDep{% if auth %}, get_current_principal{% endif %}{% if rbac %}, require_permission{% endif %}
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.realtime.broker import BrokerUnavailable, InvalidEventId
from app.schemas.v1.event import EventBatch, EventRead

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

@router.get(
    "/poll",
    response_model=EventBatch,
    responses={204: {"description": "No event arrived before the timeout."}}{% if rbac %},
    dependencies=READ{% endif %},
)
async def poll_events(
    broker: BrokerDep,
    channel: ChannelDep,
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
    try:
        if after is None:
            return EventBatch(events=[], last_id=await broker.latest_id(channel))
        wait = min(timeout, get_settings().long_poll_max_seconds)
        events = await broker.read(channel, after, wait)
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
