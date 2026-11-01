"""Per-process state shared by every long-lived connection (SSE, long polls, ...)."""
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.exceptions import AppError


class RealtimeState:
    """Caps concurrent long-lived connections and tells them when the app shuts down.

    ``slot()`` is held for as long as a connection lives; ``closing`` is set at shutdown
    so handlers end by themselves instead of waiting for their clients to leave.
    """

    def __init__(self, max_connections: int) -> None:
        if max_connections < 1:
            raise ValueError("max_connections must be at least 1")
        self.max_connections = max_connections
        self.active = 0
        self.closing = asyncio.Event()

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Take a slot, or fail with 503 and ``Retry-After`` when none is free."""
        if self.active >= self.max_connections:
            raise AppError(
                503,
                "too_many_connections",
                "Too many open connections",
                headers={"Retry-After": "5"},
            )
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1
