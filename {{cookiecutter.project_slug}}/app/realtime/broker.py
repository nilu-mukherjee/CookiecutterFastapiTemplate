{%- set tenancy = cookiecutter.use_auth == "y" and cookiecutter.use_multitenancy == "y" -%}
"""Realtime item events: the broker shared by every streaming or polling endpoint.

Contract (identical for every implementation, tested by one suite):

* Events of a channel have strictly increasing ids.
* ``read(channel, after_id=X, ...)`` returns the events with an id after X, oldest first
  (at most ``count``), waiting up to ``block_seconds`` for at least one when there are
  none yet; it returns ``[]`` on timeout. ``after_id=None`` means "only events published
  after this call starts". An ``after_id`` this implementation cannot have issued raises
  ``InvalidEventId``.
* Each channel retains a bounded window of events (``maxlen``). A cursor older than the
  window gets the retained events, so a client that fell behind can miss events (a gap)
  without being told; it should re-fetch the resource list when that matters.
* ``latest_id(channel)`` is the id of the newest retained event, or ``None`` when the
  channel has none; ``start_id`` is a valid id that precedes every event.

Events carry ids only, never item contents: clients re-fetch the item through the API{% if cookiecutter.use_auth == "y" %},
where the usual access checks apply{% endif %}.
"""
import asyncio
import re
from collections import deque
from dataclasses import dataclass
from itertools import islice
from typing import Any, Protocol
{%- if tenancy %}
from uuid import UUID
{%- endif %}


@dataclass(frozen=True, slots=True)
class Event:
    id: str
    type: str
    data: dict[str, Any]


class InvalidEventId(ValueError):
    """A client-supplied position is not an event id of this broker."""


class BrokerUnavailable(Exception):
    """The broker's backing store cannot be reached."""


class EventBroker(Protocol):
    start_id: str
    """A valid id that precedes every event of every channel (read from here to get all)."""

    async def publish(self, channel: str, event_type: str, data: dict[str, Any]) -> str:
        """Append an event to the channel and return its id."""
        ...

    async def read(
        self, channel: str, after_id: str | None, block_seconds: float, count: int = 100
    ) -> list[Event]:
        """The events after ``after_id`` (see the module docstring)."""
        ...

    async def latest_id(self, channel: str) -> str | None:
        """The id of the newest retained event of the channel, or None when it has none."""
        ...
{%- if tenancy %}


def channel_for(tenant_id: UUID | None) -> str:
    """The item channel of ONE tenant (``None``: the single shared channel).

    The tenant always comes from the authenticated principal, never from the request,
    so a client can only ever read (and a write only ever publish to) its own tenant's
    channel.
    """
    if tenant_id is None:
        return "items"
    return f"tenant:{tenant_id}:items"
{%- else %}


def channel_for(_: None) -> str:
    """The item channel."""
    return "items"
{%- endif %}


_DECIMAL = re.compile(r"[0-9]{1,20}")


class _Channel:
    __slots__ = ("events", "last_id", "published", "readers")

    def __init__(self, maxlen: int) -> None:
        self.events: deque[tuple[int, Event]] = deque(maxlen=maxlen)
        self.last_id = 0
        # Replaced on every publish, so a reader waits on the signal current when it
        # found nothing to return, and is woken by the next publish.
        self.published = asyncio.Event()
        self.readers = 0


class InMemoryBroker:
    """Per-process broker: a bounded replay window per channel, ids "1", "2", ...

    Events reach only clients connected to this process, and are lost on restart: run a
    single worker process, or enable Redis for a shared, replayable stream. Memory is
    bounded per channel (``maxlen`` events); a channel nobody reads that never had
    events is dropped.{% if tenancy %} Channels are per tenant, so the number of channels grows
    with the number of tenants that have item activity.{% endif %}
    """

    start_id = "0"

    def __init__(self, maxlen: int = 1000) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be at least 1")
        self.maxlen = maxlen
        self._channels: dict[str, _Channel] = {}

    async def publish(self, channel: str, event_type: str, data: dict[str, Any]) -> str:
        ch = self._channels.get(channel)
        if ch is None:
            ch = self._channels[channel] = _Channel(self.maxlen)
        ch.last_id += 1
        event = Event(id=str(ch.last_id), type=event_type, data=dict(data))
        ch.events.append((ch.last_id, event))
        ch.published.set()
        ch.published = asyncio.Event()
        return event.id

    async def read(
        self, channel: str, after_id: str | None, block_seconds: float, count: int = 100
    ) -> list[Event]:
        after = _parse(after_id) if after_id is not None else None
        ch = self._channels.get(channel)
        if after is None:
            after = ch.last_id if ch is not None else 0
        if ch is not None:
            found = _after(ch, after, count)
            if found:
                return found
        if block_seconds <= 0:
            return []
        if ch is None:
            ch = self._channels[channel] = _Channel(self.maxlen)
        ch.readers += 1
        try:
            async with asyncio.timeout(block_seconds):
                while True:
                    # No await between the check above (or below) and taking the
                    # signal, so a publish in between cannot be missed.
                    await ch.published.wait()
                    found = _after(ch, after, count)
                    if found:
                        return found
        except TimeoutError:
            return []
        finally:
            ch.readers -= 1
            if not ch.readers and not ch.events and self._channels.get(channel) is ch:
                del self._channels[channel]

    async def latest_id(self, channel: str) -> str | None:
        ch = self._channels.get(channel)
        return str(ch.last_id) if ch is not None and ch.last_id else None

    def waiting(self, channel: str) -> int:
        """How many reads are blocked on the channel (for tests and metrics)."""
        ch = self._channels.get(channel)
        return ch.readers if ch is not None else 0

    def channel_count(self) -> int:
        return len(self._channels)


def _parse(event_id: str) -> int:
    # ASCII digits only (str.isdigit() accepts "²"), bounded length.
    if not _DECIMAL.fullmatch(event_id):
        raise InvalidEventId("invalid event id")
    return int(event_id)


def _after(ch: _Channel, after: int, count: int) -> list[Event]:
    return list(islice((event for ident, event in ch.events if ident > after), count))
