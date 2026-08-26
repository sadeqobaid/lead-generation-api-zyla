from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from uuid import uuid4

from redis.asyncio import Redis


class RateLimitExceeded(Exception):
    """Raised when a caller has exhausted its configured request window."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__("rate limit exceeded")


class InMemoryRateLimiter:
    """Single-process limiter reserved for local development and tests."""

    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> None:
        now = time.monotonic()
        async with self._lock:
            events = self._events[key]
            while events and now - events[0] >= self.window_seconds:
                events.popleft()
            if len(events) >= self.limit:
                retry_after = max(1, int(self.window_seconds - (now - events[0])))
                raise RateLimitExceeded(retry_after)
            events.append(now)

    async def close(self) -> None:
        return None


class RedisRateLimiter:
    """Shared atomic sliding-window limiter for horizontally scaled API processes."""

    _SCRIPT = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    local member = ARGV[4]
    redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
    local count = redis.call('ZCARD', key)
    if count >= limit then
        local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
        local retry = window
        if #oldest > 1 then
            retry = math.max(1, math.ceil(window - (now - tonumber(oldest[2]))))
        end
        redis.call('EXPIRE', key, window + 1)
        return {0, retry}
    end
    redis.call('ZADD', key, now, member)
    redis.call('EXPIRE', key, window + 1)
    return {1, 0}
    """

    def __init__(self, redis_url: str, limit: int, window_seconds: int = 60) -> None:
        self.redis: Redis = Redis.from_url(redis_url, decode_responses=True)
        self.limit = limit
        self.window_seconds = window_seconds

    async def check(self, key: str) -> None:
        now = time.time()
        redis_key = f"rate:{key}"
        member = f"{now}:{uuid4().hex}"
        result = await self.redis.eval(
            self._SCRIPT,
            1,
            redis_key,
            str(now),
            str(self.window_seconds),
            str(self.limit),
            member,
        )
        allowed, retry_after = int(result[0]), int(result[1])
        if allowed != 1:
            raise RateLimitExceeded(retry_after)

    async def close(self) -> None:
        await self.redis.aclose()
