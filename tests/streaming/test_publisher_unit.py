"""event-publisher pieces that need neither a database nor Kafka."""

import asyncio
import json
import uuid

import pytest

from events.runtime import Heartbeat, serve_ops, start_with_retry
from gateway.models import EventOutbox
from publisher.sender import KafkaSender, Message
from publisher.service import backoff_delay, to_message


def test_backoff_is_exponential_and_capped():
    assert [backoff_delay(n, 1, 60) for n in range(1, 9)] == [1, 2, 4, 8, 16, 32, 60, 60]
    for _ in range(100):
        assert 0.8 <= backoff_delay(1, 1, 60, jitter=0.2) <= 1


def test_to_message_keys_by_tenant_and_sets_headers():
    tenant = uuid.uuid4()
    row = EventOutbox(
        event_id=uuid.uuid4(),
        tenant_id=tenant,
        topic="governance.events.raw",
        partition_key=str(tenant),
        payload={"schema_version": "1.0.0", "event_type": "policy_decision", "x": "é"},
    )
    m = to_message(row)
    assert m.key == str(tenant).encode()
    assert json.loads(m.value)["x"] == "é"
    assert dict(m.headers) == {
        "schema_version": b"1.0.0",
        "source": b"gateway",
        "event_type": b"policy_decision",
    }


class _StubProducer:
    """Stands in for AIOKafkaProducer.send(): returns a future per message."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)

    async def send(self, topic, value, key=None, headers=None):
        outcome = self.outcomes.pop(0)
        if outcome == "raise":
            raise RuntimeError("buffer full")
        fut = asyncio.get_running_loop().create_future()
        if outcome == "ok":
            fut.set_result(object())
        elif outcome == "nack":
            fut.set_exception(ConnectionError("broker"))
        return fut  # "hang": never resolves


def _sender(outcomes, timeout=0.2):
    s = KafkaSender("127.0.0.1:1", timeout_s=timeout)
    s.producer = _StubProducer(outcomes)
    return s


async def test_send_many_maps_acks_and_errors_per_message():
    msgs = [Message("t", b"k", b"v")] * 3
    res = await _sender(["ok", "nack", "raise"]).send_many(msgs)
    assert res[0] is None
    assert isinstance(res[1], ConnectionError)
    assert isinstance(res[2], RuntimeError)


async def test_send_many_times_out_unacknowledged_batches():
    res = await _sender(["ok", "hang"]).send_many([Message("t", b"k", b"v")] * 2)
    assert all(isinstance(r, TimeoutError) for r in res)  # nothing is marked published


async def test_start_with_retry_backs_off_until_connected():
    calls = []

    async def start():
        calls.append(1)
        if len(calls) < 2:
            raise ConnectionError

    assert await start_with_retry(start, asyncio.Event(), lambda: None, max_delay_s=0.01)
    assert len(calls) == 2
    stop = asyncio.Event()
    stop.set()
    assert not await start_with_retry(start, stop, lambda: None)


@pytest.mark.parametrize(("stale", "status"), [(False, b"200 OK"), (True, b"503")])
async def test_ops_server_health_and_metrics(stale, status):
    hb = Heartbeat(max_age_s=-1 if stale else 60)
    server = await serve_ops(hb, 0)
    port = server.sockets[0].getsockname()[1]

    async def get(path):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        await w.drain()
        data = await r.read()
        w.close()
        return data

    try:
        assert status in (await get("/healthz")).split(b"\r\n")[0]
        assert b"python_info" in await get("/metrics") or b"# HELP" in await get("/metrics")
        assert b"404" in (await get("/nope")).split(b"\r\n")[0]
    finally:
        server.close()
