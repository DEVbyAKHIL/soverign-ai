"""Window maths of every aggregation spec, checked against plain-Python expectations.

Runs inside the Spark image (docker compose --profile test run --rm spark-tests); skipped where
pyspark is not installed. The specs are the same objects the streaming job uses.
"""

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import pytest

pyspark = pytest.importorskip("pyspark")

from pyspark.sql import SparkSession  # noqa: E402
from pyspark.sql import functions as F  # noqa: E402, N812

from producer.generator import EventGenerator  # noqa: E402
from streaming.aggregations import SPECS, aggregate, enrich, parse, prepare, to_rows  # noqa: E402
from streaming.fields import ddl  # noqa: E402
from streaming.validation import ALL_AGENTS  # noqa: E402

UTC = timezone.utc  # noqa: UP017 - Python 3.10 in the Spark image
T0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
SPEC = {s.name: s for s in SPECS}


@pytest.fixture(scope="module")
def spark():
    s = (
        SparkSession.builder.master("local[2]")
        .appName("aggregation-tests")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield s
    s.stop()


@pytest.fixture(scope="module")
def events():
    gen = EventGenerator(seed=11)
    # 3000 events over 20 minutes, unordered within the span.
    return [gen.event(T0 + timedelta(seconds=gen.rng.uniform(0, 1200))) for _ in range(3000)]


@pytest.fixture(scope="module")
def frame(spark, events):
    raw = spark.createDataFrame([(json.dumps(e),) for e in events], "value string")
    return enrich(raw.select(parse(F.col("value"), ddl()).alias("e")).select("e.*"))


def _ts(e):
    return datetime.fromisoformat(e["event_timestamp"].replace("Z", "+00:00"))


def _floor(ts, minutes):
    epoch = int(ts.timestamp())
    return epoch - epoch % (minutes * 60)


def _sliding_starts(ts, length_min, slide_min=1):
    first = _floor(ts, slide_min) - (length_min - slide_min) * 60
    return [first + i * slide_min * 60 for i in range(length_min // slide_min)]


def _collect(frame, name):
    return [r.asDict() for r in aggregate(frame, SPEC[name]).collect()]


def test_parse_keeps_every_event(frame, events):
    assert frame.count() == len(events)
    assert frame.filter(F.col("event_ts").isNull()).count() == 0


def test_tumbling_requests_per_tenant(frame, events):
    expected = Counter((_floor(_ts(e), 1), e["tenant_id"]) for e in events)
    got = {
        (int(r["window_start"].replace(tzinfo=UTC).timestamp()), r["tenant_id"]): r["event_count"]
        for r in _collect(frame, "requests_per_tenant_1m")
    }
    assert got == dict(expected)
    assert sum(got.values()) == len(events)  # tumbling: every event in exactly one window


def test_requests_per_agent_excludes_non_agents(frame, events):
    agent_events = [e for e in events if e["agent_id"]]
    rows = _collect(frame, "requests_per_agent_1m")
    assert sum(r["event_count"] for r in rows) == len(agent_events)
    assert all(r["agent_id"] for r in rows)


def test_decisions_by_type(frame, events):
    rows = _collect(frame, "decisions_by_type_1m")
    totals = Counter()
    for r in rows:
        totals[r["decision"]] += r["event_count"]
    assert totals == Counter(e["decision"] for e in events)


def test_sliding_deny_rate_latency_and_p99(frame, events):
    buckets = defaultdict(list)
    for e in events:
        for start in _sliding_starts(_ts(e), 5):
            buckets[(start, e["tenant_id"])].append(e)
    rows = _collect(frame, "tenant_health_5m")
    assert len(rows) == len(buckets)
    for r in rows:
        key = (int(r["window_start"].replace(tzinfo=UTC).timestamp()), r["tenant_id"])
        evs = buckets[key]
        assert r["window_end"] - r["window_start"] == timedelta(minutes=5)
        assert r["event_count"] == len(evs)
        deny = sum(e["decision"] in ("deny", "quarantine") for e in evs) / len(evs)
        assert math.isclose(r["deny_rate"], deny, rel_tol=1e-9)
        lat = sorted(e["latency_ms"] for e in evs)
        assert math.isclose(r["avg_ms"], sum(lat) / len(lat), rel_tol=1e-9)
        assert r["max_ms"] == lat[-1]
        # percentile_approx returns an actual sample at or near the 99th rank.
        rank = lat.index(r["p99_ms"])
        assert abs(rank - math.ceil(0.99 * len(lat)) + 1) <= max(2, len(lat) // 100)
        assert r["failed"] == sum(e["status"] == "failed" for e in evs)


def test_each_event_lands_in_five_sliding_windows(frame, events):
    rows = _collect(frame, "tenant_health_5m")
    assert sum(r["event_count"] for r in rows) == 5 * len(events)


def test_pii_per_model_tumbling_5m(frame, events):
    expected = defaultdict(int)
    for e in events:
        expected[(_floor(_ts(e), 5), e["tenant_id"], e["destination_model"])] += e["pii_count"]
    rows = _collect(frame, "pii_per_model_5m")
    got = {
        (
            int(r["window_start"].replace(tzinfo=UTC).timestamp()),
            r["tenant_id"],
            r["destination_model"],
        ): r["pii_entities"]
        for r in rows
    }
    assert got == dict(expected)


def test_agent_risk_score_bounds(frame):
    rows = _collect(frame, "agent_risk_10m")
    assert rows
    for r in rows:
        assert 0.0 <= r["anomaly_score"] <= 1.0
        assert r["window_end"] - r["window_start"] == timedelta(minutes=10)


def test_sovereignty_tumbling_10m_sums_to_total(frame, events):
    rows = _collect(frame, "sovereignty_10m")
    assert sum(r["event_count"] for r in rows) == len(events)
    assert {r["destination_jurisdiction"] for r in rows} <= {"eu", "non_eu", "unknown"}


def test_streaming_dedup_then_aggregate_in_update_mode(spark, events, tmp_path):
    """The production chain on a real stream: every event delivered twice, counted once."""
    src = tmp_path / "in"
    src.mkdir()
    lines = [json.dumps(e) for e in events]
    (src / "part-0.json").write_text("\n".join(lines + lines) + "\n", encoding="utf-8")
    stream = spark.readStream.schema("value string").text(str(src))
    parsed = stream.select(parse(F.col("value"), ddl()).alias("e")).select("e.*")
    agg = aggregate(prepare(parsed, "2 minutes"), SPEC["requests_per_tenant_1m"])
    q = (
        agg.writeStream.outputMode("update")
        .format("memory")
        .queryName("dedup_check")
        .option("checkpointLocation", str(tmp_path / "cp"))
        .start()
    )
    try:
        q.processAllAvailable()
    finally:
        q.stop()
    rows = spark.table("dedup_check").collect()
    assert sum(r["event_count"] for r in rows) == len(events)


def test_to_rows_long_format(frame):
    spec = SPEC["requests_per_tenant_1m"]
    records = aggregate(frame, spec).limit(3).collect()
    rows = to_rows(spec, records, batch_id=42)["aggregated_request_metrics"]
    assert len(rows) == 2 * len(records)  # requests + tokens per window
    start, end, wtype, tenant, agent, metric, value, count, query, batch = rows[0]
    assert start.tzinfo is not None and end - start == timedelta(minutes=1)
    assert (wtype, agent, query, batch) == ("tumbling", ALL_AGENTS, spec.name, 42)
    assert metric in ("requests", "tokens") and value >= 0 and count >= 1
