"""Governance event contract (schemas/governance_event/1.0.0.json)."""

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from jsonschema import Draft202012Validator

from events.builder import ACTION_TO_DECISION, caller_key, decision_event, pseudonymous_id
from events.schema import SCHEMA_VERSION, errors, format_ts, load_schema, parse_ts
from policy.engine import PRECEDENCE

TENANT = uuid.UUID("11111111-1111-4111-8111-111111111111")
CALLER = uuid.UUID("33333333-3333-4333-8333-333333333333")


def _event(**over):
    base = dict(
        event_id=uuid.uuid4(),
        timestamp=datetime(2026, 11, 6, 19, 0, 0, 123456, tzinfo=UTC),
        tenant_id=TENANT,
        agent_id=None,
        caller_type="user",
        caller_pseudonym=pseudonymous_id(caller_key("k" * 40), TENANT, CALLER),
        action_taken="pseudonymise",
        endpoint="infer",
        destination_model="gpt-4o",
        destination_jurisdiction="eu",
        declared_purpose="customer_support_summarisation",
        sensitivity_level="confidential",
        pii_count=2,
        injection_risk_score=0.12,
        policy_ids=["pseudonymise_customer_pii@1.1.0"],
        latency_ms=42,
        tokens_processed=120,
        metadata={"final_decision": "allow_with_modification", "review_status": "not_required"},
    )
    base.update(over)
    return decision_event(**base)


def test_schema_is_valid_draft_2020_12():
    Draft202012Validator.check_schema(load_schema())


def test_built_event_is_valid():
    ev = _event()
    assert errors(ev) == []
    assert ev["schema_version"] == SCHEMA_VERSION
    assert ev["decision"] == "redact"
    assert ev["event_timestamp"] == "2026-11-06T19:00:00.123Z"
    assert ev["metadata"]["source"] == "gateway"


def test_every_policy_action_maps_to_a_schema_decision():
    assert set(ACTION_TO_DECISION) == set(PRECEDENCE)
    for action in PRECEDENCE:
        ev = _event(action_taken=action)
        assert errors(ev) == [], action
        assert ev["status"] == ("blocked" if action in ("deny", "quarantine") else "completed")


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda e: e.pop("tenant_id"), "<root>:required"),
        (lambda e: e.update(prompt="hello"), "<root>:additionalProperties"),
        (lambda e: e["metadata"].update(email="a@b.c"), "metadata:additionalProperties"),
        (lambda e: e.update(decision="maybe"), "decision:enum"),
        (lambda e: e.update(event_timestamp="2026-11-06T19:00:00.123"), "event_timestamp:pattern"),
        (
            lambda e: e.update(event_timestamp="2026-11-06T19:00:00+02:00"),
            "event_timestamp:pattern",
        ),  # noqa: E501
        (lambda e: e.update(tenant_id="tenant-1"), "tenant_id:pattern"),
        (lambda e: e.update(latency_ms=-1), "latency_ms:minimum"),
        (lambda e: e.update(injection_risk_score=1.5), "injection_risk_score:maximum"),
        (lambda e: e.update(schema_version="2.0.0"), "schema_version:const"),
    ],
)
def test_invalid_events_are_rejected(mutate, reason):
    ev = _event()
    mutate(ev)
    assert reason in errors(ev)


def test_errors_never_echo_values():
    ev = _event()
    ev["declared_purpose"] = "jane.doe@example.com"  # fails the identifier pattern
    assert all("jane" not in e for e in errors(ev))


def test_caller_pseudonym_is_keyed_and_stable():
    a = pseudonymous_id(caller_key("k" * 40), TENANT, CALLER)
    assert a == pseudonymous_id(caller_key("k" * 40), TENANT, CALLER)
    assert a != pseudonymous_id(caller_key("j" * 40), TENANT, CALLER)  # depends on the key
    assert a != str(CALLER)
    uuid.UUID(a)


def test_timestamp_helpers():
    dt = datetime(2026, 11, 6, 20, 0, 0, 5000, tzinfo=timezone(timedelta(hours=1)))
    assert format_ts(dt) == "2026-11-06T19:00:00.005Z"
    assert parse_ts("2026-11-06T19:00:00.005Z") == dt
    with pytest.raises(ValueError):
        format_ts(datetime(2026, 11, 6))  # naive
    with pytest.raises(ValueError):
        parse_ts("2026-11-06T19:00:00.005+02:00")
