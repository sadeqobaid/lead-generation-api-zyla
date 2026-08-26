#!/usr/bin/env python3
"""Run the production background worker.

The worker consumes Redis Streams messages from the configured consumer group. Each message
references a database-backed BackgroundJob. The database is the source of truth for job state;
Redis is the delivery and coordination layer. Run one or more copies with distinct consumers.

Usage:
    python scripts/worker.py
    python scripts/worker.py --once

Required production settings include DATABASE_URL, REDIS_URL, QUEUE_BACKEND=redis,
QUEUE_STREAM, QUEUE_GROUP, and QUEUE_CONSUMER. Set MAX_JOB_ATTEMPTS to bound retries.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import SessionLocal
from app.jobs import execute_job
from app.models import BackgroundJob
from app.queue import QueueMessage, RedisJobQueue


logger = logging.getLogger("lead_generation_worker")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def process_message(message: QueueMessage, max_attempts: int) -> tuple[bool, bool]:
    """Process one message and return whether it was successfully acknowledged."""
    with SessionLocal() as db:
        job = db.get(BackgroundJob, message.job_id)
        if job is None:
            logger.warning("job missing; acknowledging message", extra={"job_id": message.job_id})
            return True, False
        if job.status == "SUCCEEDED":
            return True, False
        job.status = "RUNNING"
        job.attempts += 1
        job.updated_at = utcnow()
        db.commit()
        try:
            result = execute_job(db, job)
        except Exception as exc:  # pragma: no cover - exercised by deployment failures
            job.status = "FAILED" if job.attempts >= max_attempts else "QUEUED"
            job.last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            job.updated_at = utcnow()
            db.commit()
            logger.exception(
                "background job failed",
                extra={"job_id": job.id, "kind": job.kind, "attempt": job.attempts, "retrying": job.status == "QUEUED"},
            )
            return False, job.status == "QUEUED"
        job.status = "SUCCEEDED"
        job.last_error = None
        job.completed_at = utcnow()
        job.updated_at = utcnow()
        db.commit()
        logger.info("background job completed", extra={"job_id": job.id, "kind": job.kind, "result": result})
        return True, False


async def requeue_if_needed(queue: RedisJobQueue, message: QueueMessage, should_retry: bool) -> None:
    """Acknowledge the failed delivery and enqueue a fresh delivery when attempts remain."""
    if not should_retry:
        return
    await queue.enqueue(message.job_id, message.kind)


async def run_worker(once: bool = False, max_attempts: int = 3) -> None:
    """Consume pending and new jobs until SIGTERM/SIGINT or one cycle in once mode."""
    if settings.queue_backend != "redis":
        raise RuntimeError("worker requires QUEUE_BACKEND=redis")
    queue = RedisJobQueue(settings.redis_url, settings.queue_stream, settings.queue_group, settings.queue_consumer, settings.queue_block_ms)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        try:
            loop.add_signal_handler(getattr(signal, signal_name), stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        pending = await queue.claim_pending()
        for message in pending:
            succeeded, should_retry = process_message(message, max_attempts)
            await queue.acknowledge(message.message_id)
            await requeue_if_needed(queue, message, should_retry)
        if once:
            return
        while not stop.is_set():
            for message in await queue.read():
                succeeded, should_retry = process_message(message, max_attempts)
                await queue.acknowledge(message.message_id)
                await requeue_if_needed(queue, message, should_retry)
                if stop.is_set():
                    break
    finally:
        await queue.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Consume Lead Generation API background jobs from Redis Streams.")
    parser.add_argument("--once", action="store_true", help="Process pending messages and one read cycle, then exit.")
    parser.add_argument("--max-attempts", type=int, default=3, help="Maximum attempts before marking a job FAILED (default: 3).")
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = parse_args()
    asyncio.run(run_worker(once=args.once, max_attempts=max(1, args.max_attempts)))
