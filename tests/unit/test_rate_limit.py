from unittest.mock import MagicMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from gateway.core.errors import RateLimitExceeded
from gateway.core.rate_limit import RateLimiter


async def test_allows_under_limit_then_blocks(fake_redis):
    limiter = RateLimiter(fake_redis, limit_per_minute=3)
    for i in range(1, 4):
        assert await limiter.hit("t", "c") == i
    with pytest.raises(RateLimitExceeded):
        await limiter.hit("t", "c")


async def test_keys_are_isolated(fake_redis):
    limiter = RateLimiter(fake_redis, limit_per_minute=1)
    await limiter.hit("tenant-a")
    assert await limiter.hit("tenant-b") == 1


async def test_fail_closed_when_redis_down():
    broken = MagicMock()
    broken.pipeline.side_effect = RedisConnectionError("down")
    with pytest.raises(RateLimitExceeded):
        await RateLimiter(broken, 10).hit("x")


async def test_fail_open_when_configured():
    broken = MagicMock()
    broken.pipeline.side_effect = RedisConnectionError("down")
    assert await RateLimiter(broken, 10, fail_closed=False).hit("x") == 0
