import math
import time
from collections.abc import Callable
from typing import Protocol

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

WINDOW_SECONDS = 60.0
EXEMPT_PREFIX = "/health/"


class RateLimiter(Protocol):
    async def hit(self, key: str) -> bool:
        """Register a request for `key`; True means it is allowed."""
        ...

    async def retry_after(self, key: str) -> int:
        """Whole seconds until the key's window resets (minimum 1)."""
        ...


class InMemoryRateLimiter:
    """Fixed 60 second window per key. Per-process; use a shared store to scale out."""

    def __init__(
        self, limit_per_minute: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit_per_minute
        self._clock = clock
        # key -> (window_start, count)
        self._windows: dict[str, tuple[float, int]] = {}
        self._next_sweep = 0.0

    async def retry_after(self, key: str) -> int:
        entry = self._windows.get(key)
        if entry is None:
            return 1
        return max(1, math.ceil(entry[0] + WINDOW_SECONDS - self._clock()))

    async def hit(self, key: str) -> bool:
        now = self._clock()
        if now >= self._next_sweep:
            self._windows = {
                k: v for k, v in self._windows.items() if v[0] + WINDOW_SECONDS > now
            }
            self._next_sweep = now + WINDOW_SECONDS
        start, count = self._windows.get(key, (now, 0))
        if start + WINDOW_SECONDS <= now:
            start, count = now, 0
        if count >= self.limit:
            self._windows[key] = (start, count)
            return False
        self._windows[key] = (start, count + 1)
        return True


class RateLimitMiddleware:
    """Key = client IP from the ASGI scope (X-Forwarded-For is deliberately ignored)."""

    def __init__(self, app: ASGIApp, limiter: RateLimiter) -> None:
        self.app = app
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].startswith(EXEMPT_PREFIX):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        key = client[0] if client else "unknown"
        if await self.limiter.hit(key):
            await self.app(scope, receive, send)
            return

        retry_after = await self.limiter.retry_after(key)
        response = JSONResponse(
            {
                "code": "rate_limited",
                "detail": "Too many requests",
                "request_id": scope.get("state", {}).get("request_id"),
            },
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
        await response(scope, receive, send)
