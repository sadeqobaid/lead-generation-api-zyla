from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from redis.asyncio import Redis


@dataclass(frozen=True)
class IdempotencyHit:
    fingerprint: str
    response_body: dict[str, Any]
    response_status: int = 200


class IdempotencyConflict(Exception):
    """Raised when a key is reused for a different request payload."""


class IdempotencyInProgress(Exception):
    """Raised when a request is still being completed by another API replica."""


class InMemoryIdempotencyStore:
    """Single-process implementation reserved for local development and tests."""

    def __init__(self, ttl_seconds: int = 86_400) -> None:
        self.ttl_seconds = ttl_seconds
        self._records: dict[str, tuple[datetime, IdempotencyHit | None]] = {}
        self._lock = asyncio.Lock()

    async def reserve(self, scope_key: str, fingerprint: str) -> bool:
        async with self._lock:
            record = self._records.get(scope_key)
            if record is not None and record[0] > datetime.now(timezone.utc):
                hit = record[1]
                if hit is not None and hit.fingerprint != fingerprint:
                    raise IdempotencyConflict
                return False
            self._records[scope_key] = (
                datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds),
                None,
            )
            return True

    async def get(self, scope_key: str, fingerprint: str) -> IdempotencyHit | None:
        async with self._lock:
            record = self._records.get(scope_key)
            if record is None or record[0] <= datetime.now(timezone.utc):
                self._records.pop(scope_key, None)
                return None
            hit = record[1]
            if hit is None:
                raise IdempotencyInProgress
            if hit.fingerprint != fingerprint:
                raise IdempotencyConflict
            return hit

    async def put(self, scope_key: str, hit: IdempotencyHit) -> None:
        async with self._lock:
            self._records[scope_key] = (
                datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds),
                hit,
            )

    async def delete(self, scope_key: str) -> None:
        async with self._lock:
            self._records.pop(scope_key, None)

    async def close(self) -> None:
        return None


class RedisIdempotencyStore:
    """Shared idempotency store for horizontally scaled API processes."""

    def __init__(self, redis_url: str, ttl_seconds: int = 86_400) -> None:
        self.redis: Redis = Redis.from_url(redis_url, decode_responses=True)
        self.ttl_seconds = ttl_seconds

    def _key(self, scope_key: str) -> str:
        return f"idempotency:{scope_key}"

    async def reserve(self, scope_key: str, fingerprint: str) -> bool:
        pending = json.dumps({"state": "pending", "fingerprint": fingerprint}, separators=(",", ":"))
        created = await self.redis.set(self._key(scope_key), pending, ex=self.ttl_seconds, nx=True)
        if created:
            return True
        record = await self.redis.get(self._key(scope_key))
        if record is None:
            return await self.reserve(scope_key, fingerprint)
        payload = json.loads(record)
        if payload.get("fingerprint") != fingerprint:
            raise IdempotencyConflict
        return False

    async def get(self, scope_key: str, fingerprint: str) -> IdempotencyHit | None:
        encoded = await self.redis.get(self._key(scope_key))
        if encoded is None:
            return None
        record = json.loads(encoded)
        if record.get("fingerprint") != fingerprint:
            raise IdempotencyConflict
        if record.get("state") == "pending":
            raise IdempotencyInProgress
        return IdempotencyHit(
            fingerprint=record["fingerprint"],
            response_body=record["response_body"],
            response_status=int(record.get("response_status", 200)),
        )

    async def put(self, scope_key: str, hit: IdempotencyHit) -> None:
        encoded = json.dumps(
            {
                "state": "completed",
                "fingerprint": hit.fingerprint,
                "response_body": hit.response_body,
                "response_status": hit.response_status,
            },
            separators=(",", ":"),
        )
        # The reservation prevents another replica from replacing the completed response.
        await self.redis.set(self._key(scope_key), encoded, ex=self.ttl_seconds, xx=True)

    async def delete(self, scope_key: str) -> None:
        await self.redis.delete(self._key(scope_key))

    async def close(self) -> None:
        await self.redis.aclose()
