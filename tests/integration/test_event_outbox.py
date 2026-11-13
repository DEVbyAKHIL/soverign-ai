"""Decision -> outbox persistence: one transaction, value-free payload (needs TEST_DATABASE_URL)."""

import json
import uuid

import pytest

from events.schema import errors
from gateway.config import get_settings
from gateway.core.security import create_access_token
from gateway.services import decisions as decisions_service
from tests.conftest import raw_sql

pytestmark = pytest.mark.usefixtures("clean_db")

TENANT = uuid.UUID("dddddddd-0000-4000-8000-000000000007")
SUBJECT = uuid.UUID("eeeeeeee-0000-4000-8000-000000000008")
EMAIL = "jane.doe@example.com"


def _headers():
    token = create_access_token(subject=SUBJECT, tenant_id=TENANT, role="developer")
    return {"Authorization": f"Bearer {token}"}


def _body(**over):
    body = {
        "request_id": str(uuid.uuid4()),
        "tenant_id": str(TENANT),
        "caller_type": "user",
        "caller_id": str(SUBJECT),
        "requested_model": "gpt-4o",
        "requested_endpoint": "https://api.example.com/v1/chat",
        "destination_jurisdiction": "eu",
        "declared_purpose": "customer_support_summarisation",
        "data_classification": "internal",
        "prompt": f"Summarise the ticket from {EMAIL} about the late delivery.",
    }
    body.update(over)
    return body


async def _outbox():
    return await raw_sql(
        "select event_id, topic, partition_key, payload::text as p from event_outbox"
    )


async def test_decision_and_outbox_event_are_stored_together(client):
    r = await client.post("/v1/ai/infer", json=_body(), headers=_headers())
    assert r.status_code == 200, r.text
    decision_id = r.json()["decision_id"]
    [row] = await _outbox()
    assert str(row["event_id"]) == decision_id  # event_id == decision id: dedup key downstream
    assert row["topic"] == "governance.events.raw"
    assert row["partition_key"] == str(TENANT)
    event = json.loads(row["p"])
    assert errors(event) == []
    assert event["decision"] == "redact" and event["pii_count"] >= 1
    assert event["metadata"]["final_decision"] == "allow_with_modification"
    [d] = await raw_sql("select created_at from decisions where id = $1", uuid.UUID(decision_id))
    assert d["created_at"] is not None


async def test_outbox_payload_carries_no_sensitive_values(client):
    r = await client.post("/v1/ai/infer", json=_body(), headers=_headers())
    assert r.status_code == 200
    [row] = await _outbox()
    text = row["p"]
    for forbidden in (EMAIL, "late delivery", str(SUBJECT), "redaction_map", "[EMAIL"):
        assert forbidden not in text
    event = json.loads(text)
    assert event["caller_id"] != str(SUBJECT)  # keyed HMAC pseudonym


async def test_denied_decision_is_published_as_blocked(client):
    body = _body(destination_jurisdiction="non_eu")
    assert (await client.post("/v1/ai/infer", json=body, headers=_headers())).status_code == 200
    event = json.loads((await _outbox())[0]["p"])
    assert (event["decision"], event["status"]) == ("deny", "blocked")
    assert event["destination_jurisdiction"] == "non_eu"


async def test_duplicate_request_leaves_a_single_outbox_event(client):
    body = _body()
    assert (await client.post("/v1/ai/infer", json=body, headers=_headers())).status_code == 200
    assert (await client.post("/v1/ai/infer", json=body, headers=_headers())).status_code == 409
    assert len(await _outbox()) == 1


async def test_outbox_failure_rolls_back_the_decision(client, monkeypatch):
    real = decisions_service.outbox_row

    def broken(*a, **kw):
        row = real(*a, **kw)
        row.topic = None  # NOT NULL violation at commit
        return row

    monkeypatch.setattr(decisions_service, "outbox_row", broken)
    with pytest.raises(Exception):  # noqa: B017,PT011 - the ASGI transport re-raises the DB error
        await client.post("/v1/ai/infer", json=_body(), headers=_headers())
    assert await raw_sql("select id from decisions") == []
    assert await raw_sql("select id from audit_events") == []
    assert await _outbox() == []


async def test_outbox_can_be_disabled(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "event_outbox_enabled", False)
    assert (await client.post("/v1/ai/infer", json=_body(), headers=_headers())).status_code == 200
    assert await _outbox() == []
    assert len(await raw_sql("select id from decisions")) == 1
