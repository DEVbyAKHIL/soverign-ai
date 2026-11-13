"""Retention job: FK-safe deletes, audit-chain anchoring, dry run, run log (needs a test DB)."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from audit.hash_chain import GENESIS_HASH, compute_hash, hashed_fields
from audit.service import verify_tenant_chain
from ops.retention import RetentionSettings, run
from tests.conftest import TEST_DATABASE_URL, _exec, raw_sql

pytestmark = pytest.mark.usefixtures("clean_db")

TENANT = uuid.UUID("abababab-0000-4000-8000-00000000000a")
NOW = datetime.now(UTC)
OLD = NOW - timedelta(days=400)
D_OLD1, D_OLD2, D_NEW = (uuid.uuid4() for _ in range(3))
AGENT = uuid.uuid4()


def _decision_sql(did: uuid.UUID, ts: datetime) -> str:
    return (
        "insert into decisions (id, request_id, tenant_id, caller_id, destination_model, "
        "destination_jurisdiction, declared_purpose, input_risk_score, agent_anomaly_score, "
        "final_decision, action_taken, explanation, created_at) values "
        f"('{did}', '{uuid.uuid4()}', '{TENANT}', '{uuid.uuid4()}', 'm', 'eu', 'p', 0, 0, "
        f"'allow', 'allow', 'x', '{ts.isoformat()}')"
    )


def _detection_sql(did: uuid.UUID, ts: datetime) -> str:
    return (
        "insert into detections (id, decision_id, entity_type, entity_value_hash, confidence, "
        "detector, model_version, start_offset, end_offset, created_at) values "
        f"('{uuid.uuid4()}', '{did}', 'EMAIL', 'h', 0.9, 'regex', '1', 0, 5, '{ts.isoformat()}')"
    )


async def _insert_chain(events: list[tuple[uuid.UUID, datetime]]) -> list[str]:
    """Insert a valid hash chain for TENANT; returns the event hashes in order."""
    conn = await asyncpg.connect(
        TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    )
    hashes, previous = [], GENESIS_HASH
    try:
        for i, (decision_id, ts) in enumerate(events):
            eid, actor, data = uuid.uuid4(), uuid.uuid4(), {"n": i}
            h = compute_hash(
                previous,
                hashed_fields(
                    event_id=eid, tenant_id=TENANT, decision_id=decision_id,
                    event_type="decision.created", actor_id=actor, event_data=data, created_at=ts,
                ),
            )  # fmt: skip
            await conn.execute(
                "insert into audit_events (id, tenant_id, decision_id, event_type, actor_id, "
                "event_data, previous_hash, event_hash, created_at) "
                "values ($1, $2, $3, 'decision.created', $4, $5::jsonb, $6, $7, $8)",
                eid, TENANT, decision_id, actor, json.dumps(data), previous, h, ts,
            )  # fmt: skip
            hashes.append(h)
            previous = h
    finally:
        await conn.close()
    return hashes


@pytest.fixture
def seeded():
    old_agg = (OLD).isoformat()
    sql = [
        _decision_sql(D_OLD1, OLD), _decision_sql(D_OLD2, OLD), _decision_sql(D_NEW, NOW),
        _detection_sql(D_OLD1, OLD), _detection_sql(D_NEW, NOW),
        f"insert into agents (id, tenant_id, name, owner_id, agent_type) values "
        f"('{AGENT}', '{TENANT}', 'a', '{uuid.uuid4()}', 'assistant')",
    ]  # fmt: skip
    for ts in (NOW - timedelta(days=40), NOW):
        sql.append(
            "insert into agent_events (id, agent_id, tenant_id, request_id, event_type, "
            "tokens_processed, sensitive_fields_detected, destination, latency_ms, status, "
            f"created_at) values ('{uuid.uuid4()}', '{AGENT}', '{TENANT}', '{uuid.uuid4()}', "
            f"'infer', 1, 0, 'd', 1, 'allow', '{ts.isoformat()}')"
        )
    for published, ts in ((True, NOW - timedelta(days=10)), (False, NOW - timedelta(days=10)),
                          (True, NOW)):  # fmt: skip
        pub = f"'{ts.isoformat()}'" if published else "null"
        sql.append(
            "insert into event_outbox (event_id, tenant_id, topic, partition_key, payload, "
            f"created_at, published_at) values ('{uuid.uuid4()}', '{TENANT}', 't', 'k', '{{}}', "
            f"'{ts.isoformat()}', {pub})"
        )
    for start in (OLD, NOW):
        sql.append(
            "insert into aggregated_request_metrics (window_start, window_end, window_type, "
            "tenant_id, agent_id, metric, value, event_count, query_name, batch_id) values "
            f"('{start.isoformat()}', '{(start + timedelta(minutes=1)).isoformat()}', 'tumbling', "
            f"'{TENANT}', '{uuid.UUID(int=0)}', 'requests', 1, 1, 'q', 1)"
        )
    asyncio.run(
        _exec(
            "TRUNCATE agent_events, agents, event_outbox, aggregated_request_metrics, "
            "audit_chain_anchors, retention_runs CASCADE; " + "; ".join(sql)
        )
    )
    del old_agg
    return asyncio.run(
        _insert_chain([(D_OLD1, OLD), (D_OLD2, OLD + timedelta(seconds=1)), (D_NEW, NOW)])
    )


def _settings() -> RetentionSettings:
    return RetentionSettings(retention_database_url=TEST_DATABASE_URL)


async def _run(dry: bool, archive=None):
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        return await run(engine.connect, _settings(), dry=dry, archive=archive, now=NOW)
    finally:
        await engine.dispose()


async def _verify():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine)() as s:
            return await verify_tenant_chain(s, TENANT)
    finally:
        await engine.dispose()


async def _count(table: str) -> int:
    return (await raw_sql(f"select count(*) c from {table}"))[0]["c"]


async def test_dry_run_counts_without_deleting(seeded):
    results = {r.target: r.rows for r in await _run(dry=True)}
    assert results["audit_events"] == 2 and results["detections"] == 1
    assert results["event_outbox"] == 1 and results["agent_events"] == 1
    assert results["aggregated_request_metrics"] == 1
    assert await _count("audit_events") == 3 and await _count("decisions") == 3
    runs = await raw_sql("select bool_and(dry_run) d, count(*) n from retention_runs")
    assert runs[0]["d"] is True and runs[0]["n"] == len(results)


async def test_retention_deletes_old_data_safely(seeded, tmp_path):
    hashes = seeded
    results = {r.target: r.rows for r in await _run(dry=False, archive=tmp_path)}
    assert results["audit_events"] == 2
    # Only the new decision survives: the old ones lost their detections and audit events first.
    assert {r["id"] for r in await raw_sql("select id from decisions")} == {D_NEW}
    assert await _count("detections") == 1 and await _count("agent_events") == 1
    assert await _count("aggregated_request_metrics") == 1
    remaining = await raw_sql("select published_at is null as pending from event_outbox")
    assert sorted(r["pending"] for r in remaining) == [False, True]  # unpublished never deleted
    [anchor] = await raw_sql("select anchor_hash, pruned_events from audit_chain_anchors")
    assert (anchor["anchor_hash"], anchor["pruned_events"]) == (hashes[1], 2)
    verification = await _verify()
    assert verification.valid and verification.events_checked == 1
    assert list(tmp_path.glob("audit-*.jsonl.gz"))  # archived before deletion
    assert (await raw_sql("select count(*) c from retention_runs where not dry_run"))[0]["c"] > 0


async def test_second_run_is_a_no_op_and_chain_stays_valid(seeded):
    await _run(dry=False)
    results = {r.target: r.rows for r in await _run(dry=False)}
    assert all(v == 0 for v in results.values())
    assert (await _verify()).valid


async def test_audit_trigger_still_blocks_ordinary_deletes(seeded):
    with pytest.raises(asyncpg.RaiseError):
        await raw_sql("delete from audit_events")
    with pytest.raises(asyncpg.RaiseError):
        await raw_sql("update audit_events set event_type = 'x'")


def test_settings_validation():
    with pytest.raises(ValueError):
        RetentionSettings(retention_database_url="")
    with pytest.raises(ValueError):
        RetentionSettings(retention_database_url=TEST_DATABASE_URL, retention_agent_events_days=3)
