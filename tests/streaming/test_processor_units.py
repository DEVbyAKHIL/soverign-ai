"""Processor pieces that need neither Spark nor a database."""

import json
import random
from datetime import UTC, datetime, timedelta, timezone

import pytest

from events.schema import load_schema
from producer.failures import corrupt
from producer.generator import EventGenerator
from streaming.fields import EVENT_FIELDS, NOT_PARSED
from streaming.sink import TABLE_DIMS, upsert_sql, utc
from streaming.validation import invalid_reason


def _event(seed=1):
    return EventGenerator(seed=seed).event(datetime(2026, 11, 7, 12, tzinfo=UTC))


def test_spark_fields_mirror_the_json_schema():
    props = set(load_schema()["properties"])
    assert {n for n, _ in EVENT_FIELDS} == props - NOT_PARSED
    assert set(load_schema()["required"]) >= {n for n, _ in EVENT_FIELDS}


def test_valid_event_has_no_reason():
    assert invalid_reason(json.dumps(_event()).encode()) is None


@pytest.mark.parametrize("seed", range(30))
def test_corrupted_events_get_a_value_free_reason(seed):
    ev = _event(seed)
    value, kind = corrupt(ev, random.Random(seed))  # noqa: S311
    reason = invalid_reason(value)
    assert reason is not None, kind
    assert ev["tenant_id"] not in reason and "placeholder" not in reason


@pytest.mark.parametrize(
    ("value", "reason"),
    [(None, "empty"), (b"[1,2]", "<root>:type"), (b"\xff\xfe", "not_json"), (b"{", "not_json")],
)
def test_edge_cases(value, reason):
    assert invalid_reason(value) == reason


def test_upsert_sql_targets_the_primary_key():
    for table, dims in TABLE_DIMS.items():
        q = upsert_sql(table)
        assert f"ON CONFLICT ON CONSTRAINT pk_{table}" in q
        assert q.count("%s") == 9 + len(dims)
        assert "tenant_id" in q and all(d in q for d, _ in dims)
    with pytest.raises(KeyError):
        upsert_sql("decisions; drop table decisions")


def test_utc_normalisation():
    naive = datetime(2026, 11, 7, 12, 0)
    assert utc(naive).tzinfo is not None and utc(naive).hour == 12
    plus2 = datetime(2026, 11, 7, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    assert utc(plus2).hour == 12
