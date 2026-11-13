"""event-producer: generator, failure injection, backfill/continuous modes, verification tally."""

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from events.schema import errors, parse_ts
from producer.config import ProducerSettings
from producer.failures import KINDS, corrupt
from producer.generator import EventGenerator
from producer.run import backfill, continuous
from producer.verify import Tally


class FakeSender:
    def __init__(self, fail_every: int = 0) -> None:
        self.messages, self.fail_every, self.calls = [], fail_every, 0

    async def send_many(self, messages):
        out = []
        for m in messages:
            self.calls += 1
            if self.fail_every and self.calls % self.fail_every == 0:
                out.append(TimeoutError())
            else:
                self.messages.append(m)
                out.append(None)
        return out


def _cfg(**over) -> ProducerSettings:
    return ProducerSettings(producer_chunk=500, **over)


def _events(n=5000, seed=7):
    gen = EventGenerator(seed=seed, run_id="run1")
    now = datetime(2026, 11, 6, 12, tzinfo=UTC)
    return [gen.event(now) for _ in range(n)]


def test_generated_events_are_schema_valid():
    bad = [errors(e) for e in _events() if errors(e)]
    assert bad == []


def test_generator_is_deterministic():
    assert _events(200, seed=3) == _events(200, seed=3)
    assert _events(200, seed=3) != _events(200, seed=4)


def test_distributions_are_plausible():
    evs = _events(20_000)
    decisions = Counter(e["decision"] for e in evs)
    callers = Counter(e["caller_type"] for e in evs)
    n = len(evs)
    assert 0.45 < callers["agent"] / n < 0.65
    assert 0.4 < decisions["allow"] / n < 0.8
    assert 0.05 < decisions["deny"] / n < 0.3
    assert decisions["redact"] > 0 and decisions["require_review"] > 0
    assert decisions["quarantine"] > 0  # anomalous agents
    assert all(e["agent_id"] is None for e in evs if e["caller_type"] != "agent")
    latencies = sorted(e["latency_ms"] for e in evs)
    assert latencies[n // 2] < latencies[int(n * 0.99)] < latencies[-1]
    blocked = [e for e in evs if e["status"] == "blocked"]
    assert all(e["decision"] in ("deny", "quarantine") for e in blocked)


def test_backfill_timestamps_spread_with_bounded_disorder():
    gen = EventGenerator(seed=1)
    start = datetime(2026, 11, 5, tzinfo=UTC)
    span = timedelta(hours=24)
    total = 10_000
    ts = [gen.backfill_timestamp(i, total, start, span, 30) for i in range(total)]
    assert min(ts) >= start and max(ts) <= start + span
    for i, t in enumerate(ts):
        assert abs((t - (start + span * (i / total))).total_seconds()) <= 30


@pytest.mark.parametrize("seed", range(40))
def test_corrupted_events_are_invalid(seed):
    import random

    ev = _events(1, seed=seed)[0]
    value, kind = corrupt(ev, random.Random(seed))  # noqa: S311
    assert kind in KINDS
    try:
        decoded = json.loads(value)
    except ValueError:
        assert kind == "not_json"
        return
    assert errors(decoded), kind


async def test_backfill_counts_and_headers():
    sender = FakeSender()
    stats = await backfill(
        sender,
        _cfg(producer_failure_rate=0.02, producer_duplicate_rate=0.01),
        total=3000,
        run_id="abc",
    )
    assert stats.generated == 3000
    assert stats.valid + stats.invalid_total == 3000
    assert stats.sent == stats.acked == len(sender.messages) == 3000 + stats.duplicates
    assert stats.invalid_total > 0 and stats.duplicates > 0
    m = sender.messages[0]
    assert dict(m.headers)["run_id"] == b"abc"
    assert m.topic == "governance.events.raw"
    assert m.key == json.loads(m.value)["tenant_id"].encode()


async def test_backfill_reports_delivery_failures():
    stats = await backfill(FakeSender(fail_every=100), _cfg(), total=1000, run_id="x")
    assert stats.delivery_failed == 10
    assert stats.acked == 990


async def test_continuous_mode_respects_rate_and_stops():
    sender, stop = FakeSender(), asyncio.Event()
    task = asyncio.create_task(continuous(sender, _cfg(producer_rate=500), stop, tick_s=0.05))
    await asyncio.sleep(0.5)
    stop.set()
    stats = await task
    assert 100 <= stats.generated <= 400  # ~250 expected; generous for slow CI
    now = datetime.now(UTC)
    for m in sender.messages:
        ts = parse_ts(json.loads(m.value)["event_timestamp"])
        assert timedelta(0) <= now - ts < timedelta(seconds=10)


async def test_tally_matches_injected_failures_and_dedups_event_ids():
    sender = FakeSender()
    stats = await backfill(
        sender,
        _cfg(producer_failure_rate=0.05, producer_duplicate_rate=0.05),
        total=4000,
        run_id="t",
    )
    t = Tally()
    for m in sender.messages:
        t.add(m.value)
    assert t.read == stats.sent
    assert t.schema_valid == stats.valid + stats.duplicates
    assert sum(t.invalid.values()) == stats.invalid_total
    assert t.distinct == stats.valid
    assert t.duplicates == stats.duplicates
    assert t.ts_valid == t.schema_valid
    assert t.min_ts <= t.max_ts
