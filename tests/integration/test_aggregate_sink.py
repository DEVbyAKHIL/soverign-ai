"""Aggregate store: idempotent upserts and processor role grants (needs TEST_DATABASE_URL)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import TEST_DATABASE_URL, _exec, raw_sql

pytestmark = pytest.mark.usefixtures("clean_db")
psycopg = pytest.importorskip("psycopg")

TENANT = str(uuid.UUID("ffffffff-0000-4000-8000-000000000009"))
T0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset():
    import asyncio

    asyncio.run(
        _exec(
            "TRUNCATE aggregated_request_metrics, aggregated_policy_metrics, "
            "aggregated_latency_metrics, pipeline_progress, pipeline_run_counts"
        )
    )


@pytest.fixture
def sink():
    from streaming.sink import PostgresSink

    return PostgresSink(TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"))


def _row(
    value, batch_id, start=T0, metric="requests", agent="00000000-0000-0000-0000-000000000000"
):
    return (start, start + timedelta(minutes=1), "tumbling", TENANT, agent, metric, value,
            int(value), "requests_per_tenant_1m", batch_id)  # fmt: skip


async def _rows(table="aggregated_request_metrics"):
    return await raw_sql(
        f"select metric, value, event_count, batch_id from {table} order by metric"
    )  # noqa: S608


async def test_replaying_a_batch_does_not_duplicate_rows(sink):
    batch = {"aggregated_request_metrics": [_row(10, 1), _row(400, 1, metric="tokens")]}
    sink.write(batch, progress=("q", 1, 10, 0, 0, 2))
    sink.write(batch, progress=("q", 1, 10, 0, 0, 2))  # restart replays batch 1
    rows = await _rows()
    assert [(r["metric"], r["value"]) for r in rows] == [("requests", 10), ("tokens", 400)]
    assert len(await raw_sql("select 1 from pipeline_progress")) == 1


async def test_later_batches_overwrite_the_running_value(sink):
    sink.write({"aggregated_request_metrics": [_row(10, 1)]})
    sink.write({"aggregated_request_metrics": [_row(25, 2)]})  # update mode: full current value
    [row] = await _rows()
    assert (row["value"], row["event_count"], row["batch_id"]) == (25, 25, 2)


async def test_distinct_windows_and_dimensions_are_separate_rows(sink):
    agent = str(uuid.uuid4())
    sink.write(
        {
            "aggregated_request_metrics": [
                _row(1, 1),
                _row(2, 1, start=T0 + timedelta(minutes=1)),
                _row(3, 1, agent=agent),
            ],
            "aggregated_policy_metrics": [
                (
                    T0,
                    T0 + timedelta(minutes=5),
                    "sliding",
                    TENANT,
                    "*",
                    "deny_rate",
                    0.25,
                    8,
                    "x",
                    1,
                )
            ],
        }
    )
    assert len(await _rows()) == 3
    [p] = await _rows("aggregated_policy_metrics")
    assert (p["metric"], p["value"]) == ("deny_rate", 0.25)


async def test_run_counts_are_idempotent_per_batch(sink):
    sink.write({}, run_counts=[("run-a", 7, 100, 98, 2)])
    sink.write({}, run_counts=[("run-a", 7, 100, 98, 2)])
    sink.write({}, run_counts=[("run-a", 8, 50, 50, 0)])
    [r] = await raw_sql(
        "select sum(consumed) c, sum(valid) v, sum(invalid) i from pipeline_run_counts "
        "where run_id = 'run-a'"
    )
    assert (r["c"], r["v"], r["i"]) == (150, 148, 2)


async def test_window_constraint_rejects_inverted_windows(sink):
    nil = "00000000-0000-0000-0000-000000000000"
    bad = (T0, T0 - timedelta(minutes=1), "tumbling", TENANT, nil, "requests", 1, 1, "q", 1)
    with pytest.raises(psycopg.errors.CheckViolation):
        sink.write({"aggregated_request_metrics": [bad]})


async def test_processor_role_is_least_privilege():
    rows = await raw_sql(
        "select table_name, string_agg(privilege_type, ',' order by privilege_type) p "
        "from information_schema.role_table_grants where grantee = 'stream_processor' "
        "group by table_name"
    )
    grants = {r["table_name"]: r["p"] for r in rows}
    assert grants["aggregated_request_metrics"] == "INSERT,SELECT,UPDATE"
    assert grants["pipeline_progress"] == "INSERT,SELECT,UPDATE"
    for private in ("decisions", "audit_events", "event_outbox", "detections"):
        assert private not in grants
    assert all("DELETE" not in p for p in grants.values())
