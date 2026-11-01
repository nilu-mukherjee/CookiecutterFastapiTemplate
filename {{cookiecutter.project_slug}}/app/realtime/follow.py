"""Following a channel: the loop shared by the WebSocket and GraphQL subscriptions."""
import asyncio
from collections.abc import AsyncIterator

from app.realtime.broker import Event, EventBroker

# A wait for events is made of slices this long, so a shutdown is noticed within one.
READ_SLICE_SECONDS = 1.0


async def follow(
    broker: EventBroker, channel: str, cursor: str, closing: asyncio.Event
) -> AsyncIterator[Event]:
    """Events published after ``cursor``, as they arrive, until ``closing`` is set.

    ``BrokerUnavailable`` propagates to the caller, which decides how its protocol ends.
    """
    while not closing.is_set():
        for event in await broker.read(channel, cursor, READ_SLICE_SECONDS):
            cursor = event.id
            yield event
