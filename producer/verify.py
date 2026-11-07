"""Read a producer run back from Kafka and check every event: schema, UTC timestamp, uniqueness.

A run is identified by the `run_id` Kafka header, so other traffic on the topic (gateway events,
earlier runs) is ignored. Pure counting lives in `Tally` so it can be unit-tested without Kafka.
"""

import json
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field

from aiokafka import AIOKafkaConsumer, TopicPartition

from events.schema import errors, parse_ts, validator


@dataclass
class Tally:
    read: int = 0
    schema_valid: int = 0
    ts_valid: int = 0
    duplicates: int = 0
    invalid: Counter = field(default_factory=Counter)  # reason -> count
    min_ts: str | None = None
    max_ts: str | None = None
    _seen: set[bytes] = field(default_factory=set, repr=False)

    @property
    def distinct(self) -> int:
        return len(self._seen)

    def add(self, value: bytes) -> None:
        self.read += 1
        try:
            event = json.loads(value)
        except ValueError:
            self.invalid["not_json"] += 1
            return
        if not validator().is_valid(event):
            for reason in errors(event)[:1]:  # first violation is enough to classify
                self.invalid[reason] += 1
            return
        self.schema_valid += 1
        ts = event["event_timestamp"]
        try:
            parse_ts(ts)
        except ValueError:
            self.invalid["event_timestamp:not_utc"] += 1
            return
        self.ts_valid += 1
        self.min_ts = ts if self.min_ts is None or ts < self.min_ts else self.min_ts
        self.max_ts = ts if self.max_ts is None or ts > self.max_ts else self.max_ts
        key = uuid.UUID(event["event_id"]).bytes
        if key in self._seen:
            self.duplicates += 1
        else:
            self._seen.add(key)


async def read_run(bootstrap: str, topic: str, run_id: str, idle_timeout_s: float = 30) -> Tally:
    """Consume `topic` from the beginning up to its current end offsets."""
    consumer = AIOKafkaConsumer(
        bootstrap_servers=bootstrap,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        group_id=None,
        fetch_max_bytes=52_428_800,
        max_partition_fetch_bytes=10_485_760,
        client_id="event-verifier",
    )
    await consumer.start()
    tally = Tally()
    wanted = run_id.encode()
    try:
        ids = consumer.partitions_for_topic(topic) or await _partitions(consumer, topic)
        parts = [TopicPartition(topic, p) for p in sorted(ids)]
        consumer.assign(parts)
        await consumer.seek_to_beginning(*parts)
        end = await consumer.end_offsets(parts)
        last_progress = time.monotonic()
        while await behind(consumer, end):
            batches = await consumer.getmany(timeout_ms=1000, max_records=20_000)
            if batches:
                last_progress = time.monotonic()
            elif time.monotonic() - last_progress > idle_timeout_s:
                break
            for records in batches.values():
                for rec in records:
                    if dict(rec.headers or ()).get("run_id") == wanted:
                        tally.add(rec.value)
    finally:
        await consumer.stop()
    return tally


async def behind(consumer: AIOKafkaConsumer, end: dict[TopicPartition, int]) -> bool:
    """True while any assigned partition has not reached the captured end offset."""
    for tp, last in end.items():
        if await consumer.position(tp) < last:
            return True
    return False


async def _partitions(consumer: AIOKafkaConsumer, topic: str) -> set[int]:
    await consumer._client.force_metadata_update()  # noqa: SLF001 - no public refresh call
    return consumer.partitions_for_topic(topic) or set()
