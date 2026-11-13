"""Poll due outbox rows, publish them, mark them published only after Kafka acknowledges.

Each batch runs in one DB transaction holding row locks (FOR UPDATE SKIP LOCKED), so several
publisher replicas never send the same row concurrently. A crash after Kafka acknowledged but
before the commit re-sends the batch: delivery is at-least-once; consumers deduplicate on event_id.
"""

import asyncio
import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from events.schema import SCHEMA_VERSION
from gateway.logging_config import get_logger
from gateway.models import EventOutbox
from publisher.sender import Message, Sender

log = get_logger("event-publisher")

PUBLISHED = Counter("outbox_published_total", "Outbox events acknowledged by Kafka")
FAILED = Counter("outbox_publish_failures_total", "Outbox events that failed to publish", ["error"])
BATCH_SECONDS = Histogram("outbox_batch_seconds", "Time to publish and mark one batch")
BACKLOG = Gauge("outbox_backlog", "Unpublished outbox events")


def backoff_delay(attempts: int, base: float, maximum: float, jitter: float = 0.0) -> float:
    """Delay before the next try after `attempts` failures: base, 2*base, 4*base ... capped."""
    delay = min(maximum, base * 2 ** max(0, attempts - 1))
    return delay * (1 - jitter * random.random())  # noqa: S311  # nosec B311 - jitter


def to_message(row: EventOutbox) -> Message:
    payload = row.payload
    return Message(
        topic=row.topic,
        key=row.partition_key.encode(),
        value=json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(),
        headers=(
            ("schema_version", str(payload.get("schema_version", SCHEMA_VERSION)).encode()),
            ("source", b"gateway"),
            ("event_type", str(payload.get("event_type", "")).encode()),
        ),
    )


@dataclass(frozen=True)
class BatchResult:
    published: int
    failed: int


async def publish_once(
    sessions: async_sessionmaker[AsyncSession],
    sender: Sender,
    *,
    batch_size: int,
    backoff_base_s: float,
    backoff_max_s: float,
    jitter: float = 0.2,
) -> BatchResult:
    started = time.perf_counter()
    async with sessions() as session, session.begin():
        rows = (
            (
                await session.execute(
                    select(EventOutbox)
                    .where(
                        EventOutbox.published_at.is_(None),
                        EventOutbox.next_attempt_at <= func.now(),
                    )
                    .order_by(EventOutbox.created_at)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return BatchResult(0, 0)
        results = await sender.send_many([to_message(r) for r in rows])
        acked = [r.event_id for r, err in zip(rows, results, strict=True) if err is None]
        if acked:
            await session.execute(
                update(EventOutbox)
                .where(EventOutbox.event_id.in_(acked))
                .values(published_at=func.now(), attempts=EventOutbox.attempts + 1, last_error=None)
                .execution_options(synchronize_session=False)
            )
        now = datetime.now(UTC)
        failed = 0
        for row, err in zip(rows, results, strict=True):
            if err is None:
                continue
            failed += 1
            name = type(err).__name__
            FAILED.labels(name).inc()
            row.attempts += 1
            row.last_error = name  # class name only: never the payload or broker message text
            row.next_attempt_at = now + timedelta(
                seconds=backoff_delay(row.attempts, backoff_base_s, backoff_max_s, jitter)
            )
    PUBLISHED.inc(len(acked))
    BATCH_SECONDS.observe(time.perf_counter() - started)
    if failed:
        log.warning("outbox_publish_failed", failed=failed, published=len(acked))
    return BatchResult(len(acked), failed)


async def backlog(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with sessions() as session:
        n = await session.scalar(
            select(func.count()).select_from(EventOutbox).where(EventOutbox.published_at.is_(None))
        )
    BACKLOG.set(n or 0)
    return n or 0


async def run_loop(
    sessions: async_sessionmaker[AsyncSession],
    sender: Sender,
    stop: asyncio.Event,
    *,
    batch_size: int,
    poll_interval_s: float,
    backoff_base_s: float,
    backoff_max_s: float,
    beat: Callable[[], None] = lambda: None,
) -> None:
    """Drain due rows; sleep when idle; back off while the database itself is unreachable.
    Returns after the current batch once `stop` is set (graceful shutdown)."""
    db_failures = 0
    ticks = 0
    while not stop.is_set():
        beat()
        try:
            res = await publish_once(
                sessions,
                sender,
                batch_size=batch_size,
                backoff_base_s=backoff_base_s,
                backoff_max_s=backoff_max_s,
            )
            db_failures = 0
            if ticks % 20 == 0:
                await backlog(sessions)
            ticks += 1
            if res.published == batch_size:
                continue  # a full batch: more rows are probably waiting
            wait = poll_interval_s
        except Exception as exc:  # DB down etc.: keep the process alive, retry with backoff
            db_failures += 1
            wait = backoff_delay(db_failures, backoff_base_s, backoff_max_s, 0.2)
            log.error("outbox_loop_error", error=type(exc).__name__, retry_in_s=round(wait, 2))
        try:
            await asyncio.wait_for(stop.wait(), wait)
        except TimeoutError:
            pass
