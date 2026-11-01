from typing import Any

from pydantic import BaseModel


class EventRead(BaseModel):
    id: str
    type: str
    data: dict[str, Any]


class EventBatch(BaseModel):
    """What a long poll answers: the events, and the id to pass as ``after`` next time."""

    events: list[EventRead]
    last_id: str | None
