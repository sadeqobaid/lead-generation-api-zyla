from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError


@dataclass(frozen=True)
class QueueMessage:
    message_id: str
    job_id: str
    kind: str


class RedisJobQueue:
    """Redis Streams queue with a durable consumer group and crash recovery."""

    def __init__(self, redis_url: str, stream: str, group: str, consumer: str, block_ms: int = 5_000) -> None:
        self.redis: Redis = Redis.from_url(redis_url, decode_responses=True)
        self.stream = stream
        self.group = group
        self.consumer = consumer
        self.block_ms = block_ms

    async def ensure_group(self) -> None:
        try:
            await self.redis.xgroup_create(self.stream, self.group, id="0-0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def enqueue(self, job_id: str, kind: str) -> str:
        await self.ensure_group()
        return await self.redis.xadd(
            self.stream,
            {"job_id": job_id, "kind": kind},
            maxlen=10_000,
            approximate=True,
        )

    @staticmethod
    def _convert(message_id: str, fields: dict[str, Any]) -> QueueMessage:
        return QueueMessage(message_id=message_id, job_id=str(fields["job_id"]), kind=str(fields["kind"]))

    async def claim_pending(self, count: int = 10, min_idle_ms: int = 60_000) -> list[QueueMessage]:
        await self.ensure_group()
        result = await self.redis.xautoclaim(
            self.stream,
            self.group,
            self.consumer,
            min_idle_time=min_idle_ms,
            start_id="0-0",
            count=count,
        )
        messages = result[1] if len(result) > 1 else []
        return [self._convert(message_id, fields) for message_id, fields in messages]

    async def read(self, count: int = 10) -> list[QueueMessage]:
        await self.ensure_group()
        result = await self.redis.xreadgroup(
            self.group,
            self.consumer,
            streams={self.stream: ">"},
            count=count,
            block=self.block_ms,
        )
        messages: list[QueueMessage] = []
        for _, entries in result or []:
            messages.extend(self._convert(message_id, fields) for message_id, fields in entries)
        return messages

    async def acknowledge(self, message_id: str) -> None:
        await self.redis.xack(self.stream, self.group, message_id)

    async def close(self) -> None:
        await self.redis.aclose()
