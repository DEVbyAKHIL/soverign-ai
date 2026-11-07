"""Produce synthetic events to Kafka in backfill (fixed total) or continuous (fixed rate) mode."""

import asyncio
import json
import random
import time
from collections import Counter as Tally
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from prometheus_client import Counter

from events.schema import SCHEMA_VERSION
from producer.config import ProducerSettings
from producer.failures import corrupt
from producer.generator import EventGenerator
from publisher.sender import Message, Sender

SENT = Counter("producer_events_sent_total", "Messages handed to Kafka", ["kind"])
ACKED = Counter("producer_events_acked_total", "Messages acknowledged by Kafka")
DELIVERY_FAILED = Counter("producer_delivery_failures_total", "Messages Kafka did not acknowledge")


@dataclass
class ProduceStats:
    generated: int = 0  # events created (valid + injected invalid)
    valid: int = 0
    invalid: Tally = field(default_factory=Tally)  # injected breakage by kind
    duplicates: int = 0  # extra deliveries of an already-sent valid event
    sent: int = 0  # messages handed to Kafka (generated + duplicates)
    acked: int = 0
    delivery_failed: int = 0
    seconds: float = 0.0

    @property
    def invalid_total(self) -> int:
        return sum(self.invalid.values())


class Encoder:
    def __init__(self, cfg: ProducerSettings, run_id: str | None, stats: ProduceStats) -> None:
        self.cfg, self.stats = cfg, stats
        # Independent of the event stream; reproducible, not security-relevant.
        self.rng = random.Random(cfg.producer_seed + 1)  # noqa: S311  # nosec B311
        self.headers: tuple[tuple[str, bytes], ...] = (
            ("schema_version", SCHEMA_VERSION.encode()),
            ("source", b"producer"),
            *((("run_id", run_id.encode()),) if run_id else ()),
        )

    def encode(self, event: dict[str, Any]) -> list[Message]:
        topic, key = self.cfg.producer_topic, event["tenant_id"].encode()
        self.stats.generated += 1
        if self.rng.random() < self.cfg.producer_failure_rate:
            value, kind = corrupt(event, self.rng)
            self.stats.invalid[kind] += 1
            SENT.labels("invalid").inc()
            return [Message(topic, key, value, self.headers)]
        self.stats.valid += 1
        msg = Message(topic, key, json.dumps(event, separators=(",", ":")).encode(), self.headers)
        SENT.labels("valid").inc()
        if self.rng.random() < self.cfg.producer_duplicate_rate:
            self.stats.duplicates += 1
            SENT.labels("duplicate").inc()
            return [msg, msg]
        return [msg]


async def _flush(sender: Sender, batch: list[Message], stats: ProduceStats) -> None:
    if not batch:
        return
    results = await sender.send_many(batch)
    ok = sum(1 for r in results if r is None)
    stats.sent += len(batch)
    stats.acked += ok
    stats.delivery_failed += len(batch) - ok
    ACKED.inc(ok)
    DELIVERY_FAILED.inc(len(batch) - ok)
    batch.clear()


async def backfill(
    sender: Sender,
    cfg: ProducerSettings,
    *,
    total: int,
    run_id: str | None,
    stop: asyncio.Event | None = None,
    progress: Callable[[ProduceStats], None] = lambda s: None,
    live: bool = False,
) -> ProduceStats:
    """`total` events as fast as Kafka accepts them.

    historical (default): event times spread evenly over the last `producer_span_hours`.
    live: event time = production time minus up to `producer_disorder_s` - a high-volume burst
    that a streaming consumer with a watermark processes as on-time data (no late drops).
    """
    stats = ProduceStats()
    gen = EventGenerator(cfg.producer_seed, cfg.producer_tenants, cfg.producer_agents, run_id)
    enc = Encoder(cfg, run_id, stats)
    span = timedelta(hours=cfg.producer_span_hours)
    start = datetime.now(UTC) - span
    started = time.perf_counter()
    batch: list[Message] = []
    for i in range(total):
        if stop is not None and stop.is_set():
            break
        if live:
            ts = datetime.now(UTC) - timedelta(seconds=gen.rng.uniform(0, cfg.producer_disorder_s))
        else:
            ts = gen.backfill_timestamp(i, total, start, span, cfg.producer_disorder_s)
        batch.extend(enc.encode(gen.event(ts)))
        if len(batch) >= cfg.producer_chunk:
            await _flush(sender, batch, stats)
            progress(stats)
    await _flush(sender, batch, stats)
    stats.seconds = time.perf_counter() - started
    progress(stats)
    return stats


async def continuous(
    sender: Sender,
    cfg: ProducerSettings,
    stop: asyncio.Event,
    *,
    beat: Callable[[], None] = lambda: None,
    tick_s: float = 0.2,
) -> ProduceStats:
    """Events at `producer_rate` per second, timestamped now (minus up to 2 s of disorder)."""
    stats = ProduceStats()
    gen = EventGenerator(cfg.producer_seed, cfg.producer_tenants, cfg.producer_agents)
    enc = Encoder(cfg, None, stats)
    started = time.perf_counter()
    while not stop.is_set():
        beat()
        due = int(cfg.producer_rate * (time.perf_counter() - started)) - stats.generated
        batch: list[Message] = []
        now = datetime.now(UTC)
        for _ in range(max(0, min(due, cfg.producer_chunk))):
            batch.extend(enc.encode(gen.event(now - timedelta(seconds=gen.rng.uniform(0, 2)))))
        await _flush(sender, batch, stats)
        try:
            await asyncio.wait_for(stop.wait(), tick_s)
        except TimeoutError:
            pass
    stats.seconds = time.perf_counter() - started
    return stats
