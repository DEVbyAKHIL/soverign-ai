"""Redis fixed-window rate limiter keyed per tenant/caller."""

import time

from redis.asyncio import Redis
from redis.exceptions import RedisError

from gateway.core.errors import RateLimitExceeded
from gateway.logging_config import get_logger

log = get_logger(__name__)


class RateLimiter:
    def __init__(self, redis: Redis, limit_per_minute: int, fail_closed: bool = True) -> None:
        self.redis = redis
        self.limit = limit_per_minute
        self.fail_closed = fail_closed

    async def hit(self, *key_parts: str) -> int:
        window = int(time.time() // 60)
        key = "rl:" + ":".join(key_parts) + f":{window}"
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, 120)
                count, _ = await pipe.execute()
        except RedisError as exc:
            log.warning("rate_limiter_unavailable", fail_closed=self.fail_closed)
            if self.fail_closed:
                raise RateLimitExceeded("Rate limiter unavailable") from exc
            return 0
        if count > self.limit:
            raise RateLimitExceeded("Rate limit exceeded")
        return int(count)
