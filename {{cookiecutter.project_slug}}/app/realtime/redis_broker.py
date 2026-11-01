"""Redis Streams broker: one stream per channel, shared by every process and replayable."""
import json
import re
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.realtime.broker import BrokerUnavailable, Event, InvalidEventId

KEY_PREFIX = "events:"
_STREAM_ID = re.compile(r"([0-9]{1,20})-([0-9]{1,20})")
_MAX_PART = 2**64 - 1
_ERRORS = (RedisError, OSError, TimeoutError)


class RedisBroker:
    """``XADD events:<channel> MAXLEN ~ <maxlen> * type <t> data <json>`` / ``XREAD BLOCK``.

    Ids are Redis stream ids (``<ms>-<seq>``). ``approximate`` trimming (the default) lets
    Redis trim whole nodes, so a stream may briefly hold somewhat more than ``maxlen``
    entries; it is much cheaper than exact trimming.

    A blocking read holds its connection for up to ``block_seconds``, longer than a
    command client's short socket timeout, so reads use ``blocking_client`` (a client
    without a read timeout), and publish/latest_id use ``client``.
    """

    start_id = "0-0"

    def __init__(
        self,
        client: Redis,
        maxlen: int = 1000,
        *,
        approximate: bool = True,
        blocking_client: Redis | None = None,
    ) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be at least 1")
        self.client = client
        self.blocking_client = blocking_client or client
        self.maxlen = maxlen
        self.approximate = approximate

    async def publish(self, channel: str, event_type: str, data: dict[str, Any]) -> str:
        fields: dict[Any, Any] = {"type": event_type, "data": json.dumps(data)}
        try:
            event_id = await self.client.xadd(
                KEY_PREFIX + channel, fields, maxlen=self.maxlen, approximate=self.approximate
            )
        except _ERRORS as exc:
            raise BrokerUnavailable(type(exc).__name__) from exc
        return str(event_id)

    async def read(
        self, channel: str, after_id: str | None, block_seconds: float, count: int = 100
    ) -> list[Event]:
        if after_id is not None:
            _validate(after_id)
        # BLOCK 0 means "forever" to Redis: no BLOCK at all means "do not wait".
        block = max(1, int(block_seconds * 1000)) if block_seconds > 0 else None
        try:
            response: Any = await self.blocking_client.xread(
                {KEY_PREFIX + channel: after_id or "$"}, count=count, block=block
            )
        except _ERRORS as exc:
            raise BrokerUnavailable(type(exc).__name__) from exc
        return [
            Event(id=str(entry_id), type=fields["type"], data=json.loads(fields["data"]))
            for _stream, entries in response or []
            for entry_id, fields in entries
        ]

    async def latest_id(self, channel: str) -> str | None:
        try:
            newest: Any = await self.client.xrevrange(KEY_PREFIX + channel, count=1)
        except _ERRORS as exc:
            raise BrokerUnavailable(type(exc).__name__) from exc
        return str(newest[0][0]) if newest else None


def _validate(event_id: str) -> None:
    match = _STREAM_ID.fullmatch(event_id)
    if match is None or any(int(part) > _MAX_PART for part in match.groups()):
        raise InvalidEventId("invalid event id")
