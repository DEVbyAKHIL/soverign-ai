"""Outbox -> publisher: ack-gated marking, retry with backoff, at-least-once + dedup.

Uses a fake sender so failures are deterministic; tests/integration/test_kafka_roundtrip.py runs
the same path against a real broker.
"""

import json
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from events.schema import TOPIC_RAW
from producer.generator import EventGenerator
from producer.verify import Tally
from publisher.service import publish_once
from tests.conftest import TEST_DATABASE_URL, raw_sql

pytestmark = pytest.mark.usefixtures("clean_db")


class FakeSender:
    def __init__(self, fail=lambda i, m: None):
        self.delivered, self.fail, self.n = [], fail, 0

    async def send_many(self, messages):
        out = []
        for m in messages:
            err = self.fail(self.n, m)
            self.n += 1
            if err is None:
                self.delivered.append(m)
            out.append(err)
        return out


@pytest.fixture
async def sessions():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def seed_outbox(n: int, topic: str = TOPIC_RAW) -> list[dict]:
    from datetime import UTC, datetime

    gen = EventGenerator(seed=n)
    events = [gen.event(datetime.now(UTC)) for _ in range(n)]
    for i, ev in enumerate(events):
        await raw_sql(
            "insert into event_outbox (event_id, tenant_id, topic, partition_key, payload, "
            "created_at) values ($1, $2, $3, $4, $5::jsonb, now() + make_interval(secs => $6))",
            uuid.UUID(ev["event_id"]),
            uuid.UUID(ev["tenant_id"]),
            topic,
            ev["tenant_id"],
            json.dumps(ev),
            i / 1000,
        )
    return events


async def rows():
    return await raw_sql(
        "select event_id, attempts, published_at, last_error, next_attempt_at - now() as wait "
        "from event_outbox order by created_at"
    )


async def _publish(sessions, sender, batch=100):
    return await publish_once(
        sessions, sender, batch_size=batch, backoff_base_s=10, backoff_max_s=300, jitter=0
    )


async def test_rows_are_marked_published_only_after_ack(sessions):
    events = await seed_outbox(5)
    sender = FakeSender()
    res = await _publish(sessions, sender)
    assert (res.published, res.failed) == (5, 0)
    assert [json.loads(m.value)["event_id"] for m in sender.delivered] == [
        e["event_id"] for e in events
    ]  # oldest first
    assert all(r["published_at"] is not None and r["attempts"] == 1 for r in await rows())
    assert (await _publish(sessions, sender)).published == 0  # nothing left to send


async def test_failed_delivery_is_retried_with_exponential_backoff(sessions):
    await seed_outbox(3)
    flaky = FakeSender(fail=lambda i, m: ConnectionError() if i == 1 else None)
    res = await _publish(sessions, flaky)
    assert (res.published, res.failed) == (2, 1)
    failed = [r for r in await rows() if r["published_at"] is None]
    [r] = failed
    assert r["attempts"] == 1 and r["last_error"] == "ConnectionError"
    assert 9 < r["wait"].total_seconds() <= 10  # base delay, not yet due

    # Not due yet: a second pass sends nothing.
    assert (await _publish(sessions, FakeSender())).published == 0
    # Fail again once due: the delay doubles.
    await raw_sql("update event_outbox set next_attempt_at = now() where published_at is null")
    await _publish(sessions, FakeSender(fail=lambda i, m: TimeoutError()))
    [r] = [r for r in await rows() if r["published_at"] is None]
    assert r["attempts"] == 2 and r["last_error"] == "TimeoutError"
    assert 19 < r["wait"].total_seconds() <= 20

    await raw_sql("update event_outbox set next_attempt_at = now() where published_at is null")
    assert (await _publish(sessions, FakeSender())).published == 1
    assert all(r["published_at"] is not None for r in await rows())


async def test_batches_respect_batch_size(sessions):
    await seed_outbox(7)
    sender = FakeSender()
    assert (await _publish(sessions, sender, batch=3)).published == 3
    assert (await _publish(sessions, sender, batch=3)).published == 3
    assert (await _publish(sessions, sender, batch=3)).published == 1


async def test_crash_after_ack_redelivers_and_consumers_dedupe_by_event_id(sessions):
    """At-least-once: Kafka acked, then the publisher died before committing the mark."""
    await seed_outbox(4)

    class CrashAfterAck(FakeSender):
        async def send_many(self, messages):
            await super().send_many(messages)
            raise RuntimeError("publisher killed before commit")

    sender = CrashAfterAck()
    with pytest.raises(RuntimeError):
        await _publish(sessions, sender)
    assert all(r["published_at"] is None for r in await rows())  # transaction rolled back

    retry = FakeSender()
    assert (await _publish(sessions, retry)).published == 4
    tally = Tally()
    for m in sender.delivered + retry.delivered:  # what a consumer would see on the topic
        tally.add(m.value)
    assert tally.read == 8
    assert tally.distinct == 4 and tally.duplicates == 4


async def test_duplicate_event_id_cannot_enter_the_outbox(sessions):
    [ev] = await seed_outbox(1)
    import asyncpg

    with pytest.raises(asyncpg.UniqueViolationError):
        await raw_sql(
            "insert into event_outbox (event_id, tenant_id, topic, partition_key, payload) "
            "values ($1, $2, $3, $4, '{}'::jsonb)",
            uuid.UUID(ev["event_id"]),
            uuid.UUID(ev["tenant_id"]),
            TOPIC_RAW,
            ev["tenant_id"],
        )


async def test_concurrent_publishers_never_send_the_same_row(sessions):
    import asyncio

    await seed_outbox(40)
    a, b = FakeSender(), FakeSender()
    await asyncio.gather(_publish(sessions, a, batch=25), _publish(sessions, b, batch=25))
    ids_a = {json.loads(m.value)["event_id"] for m in a.delivered}
    ids_b = {json.loads(m.value)["event_id"] for m in b.delivered}
    assert not ids_a & ids_b
    assert len(ids_a | ids_b) == 40
