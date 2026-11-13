"""Against a real Kafka broker (KAFKA_TEST_BOOTSTRAP, set by the Compose `tests` service).

Each test uses its own throwaway topic, so the application topics are never touched.
"""

import json
import os
import uuid

import pytest

KAFKA = os.environ.get("KAFKA_TEST_BOOTSTRAP", "")
pytestmark = pytest.mark.skipif(not KAFKA, reason="KAFKA_TEST_BOOTSTRAP not set")


@pytest.fixture
async def topic():
    from aiokafka.admin import AIOKafkaAdminClient, NewTopic

    name = f"test.events.{uuid.uuid4().hex[:10]}"
    admin = AIOKafkaAdminClient(bootstrap_servers=KAFKA)
    await admin.start()
    await admin.create_topics([NewTopic(name, num_partitions=3, replication_factor=1)])
    try:
        yield name
    finally:
        await admin.delete_topics([name])
        await admin.close()


async def consume_all(topic: str):
    from aiokafka import AIOKafkaConsumer, TopicPartition

    from producer.verify import behind

    consumer = AIOKafkaConsumer(bootstrap_servers=KAFKA, group_id=None, enable_auto_commit=False)
    await consumer.start()
    try:
        await consumer._client.force_metadata_update()  # noqa: SLF001
        parts = [TopicPartition(topic, p) for p in consumer.partitions_for_topic(topic)]
        consumer.assign(parts)
        await consumer.seek_to_beginning(*parts)
        end = await consumer.end_offsets(parts)
        out = []
        while await behind(consumer, end):
            for recs in (await consumer.getmany(timeout_ms=2000)).values():
                out.extend(recs)
        return out
    finally:
        await consumer.stop()


@pytest.mark.usefixtures("clean_db")
async def test_publisher_delivers_outbox_to_kafka_and_marks_after_ack(topic):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from publisher.sender import KafkaSender
    from publisher.service import publish_once
    from tests.conftest import TEST_DATABASE_URL, raw_sql
    from tests.integration.test_outbox_publisher import seed_outbox

    events = await seed_outbox(30, topic=topic)
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    sender = KafkaSender(KAFKA, timeout_s=20)
    await sender.start()
    try:
        res = await publish_once(
            async_sessionmaker(engine), sender, batch_size=100, backoff_base_s=1, backoff_max_s=5
        )
    finally:
        await sender.stop()
        await engine.dispose()
    assert (res.published, res.failed) == (30, 0)
    assert all(r["published_at"] for r in await raw_sql("select published_at from event_outbox"))

    records = await consume_all(topic)
    assert sorted(json.loads(r.value)["event_id"] for r in records) == sorted(
        e["event_id"] for e in events
    )
    for r in records:
        ev = json.loads(r.value)
        assert r.key.decode() == ev["tenant_id"]  # partitioned by tenant
        assert dict(r.headers)["source"] == b"gateway"
    by_tenant = {}
    for r in records:
        by_tenant.setdefault(r.key, set()).add(r.partition)
    assert all(len(p) == 1 for p in by_tenant.values())  # one tenant -> one partition


async def test_acks_resolve_with_offsets(topic):
    from publisher.sender import KafkaSender, Message

    sender = KafkaSender(KAFKA, timeout_s=20)
    await sender.start()
    try:
        res = await sender.send_many([Message(topic, b"k", b"{}")] * 5)
    finally:
        await sender.stop()
    assert res == [None] * 5
    assert len(await consume_all(topic)) == 5


async def test_sending_to_a_missing_topic_fails_without_ack():
    from publisher.sender import KafkaSender, Message

    sender = KafkaSender(KAFKA, timeout_s=3)
    await sender.start()
    try:
        res = await sender.send_many([Message(f"missing.{uuid.uuid4().hex}", b"k", b"{}")])
    finally:
        await sender.stop()
    assert res[0] is not None  # auto-create is off: no ack, so the row would stay unpublished


async def test_producer_backfill_and_verify_roundtrip(topic):
    from producer.config import ProducerSettings
    from producer.run import backfill
    from producer.verify import read_run
    from publisher.sender import KafkaSender

    cfg = ProducerSettings(
        kafka_bootstrap=KAFKA,
        producer_topic=topic,
        producer_chunk=1000,
        producer_failure_rate=0.01,
        producer_duplicate_rate=0.01,
    )
    sender = KafkaSender(KAFKA, timeout_s=30, client_id="test-producer")
    await sender.start()
    try:
        stats = await backfill(sender, cfg, total=5000, run_id="rt1")
    finally:
        await sender.stop()
    assert stats.delivery_failed == 0
    t = await read_run(KAFKA, topic, "rt1")
    assert t.read == stats.acked
    assert t.distinct == stats.valid
    assert t.duplicates == stats.duplicates
    assert sum(t.invalid.values()) == stats.invalid_total
