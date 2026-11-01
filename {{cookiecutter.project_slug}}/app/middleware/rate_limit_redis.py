import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.middleware.rate_limit import WINDOW_SECONDS

logger = logging.getLogger("app.rate_limit")
KEY_PREFIX = "ratelimit:"


class RedisRateLimiter:
    """Distributed fixed 60 second window. Fails open (and logs) if Redis is unavailable."""

    def __init__(self, client: Redis, limit_per_minute: int) -> None:
        self.client = client
        self.limit = limit_per_minute

    async def hit(self, key: str) -> bool:
        name = KEY_PREFIX + key
        try:
            # MULTI: create the counter with its expiry only if absent, then INCR.
            # Atomic, so a crash can never leave a counter without a TTL.
            async with self.client.pipeline(transaction=True) as pipe:
                pipe.set(name, 0, ex=int(WINDOW_SECONDS), nx=True)
                pipe.incr(name)
                results = await pipe.execute()
        except (RedisError, OSError, TimeoutError) as exc:
            logger.warning("rate limiter unavailable, failing open: %s", exc)
            return True
        return int(results[1]) <= self.limit

    async def retry_after(self, key: str) -> int:
        try:
            ttl = int(await self.client.ttl(KEY_PREFIX + key))
        except (RedisError, OSError, TimeoutError):
            return 1
        return max(1, ttl)
