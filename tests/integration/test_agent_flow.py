"""Phase 5 end-to-end: agent registry, events, anomaly-driven policy (needs a test DB)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from gateway.core.security import create_access_token
from gateway.db import get_session
from gateway.models import AgentEvent
from tests.conftest import raw_sql

pytestmark = pytest.mark.usefixtures("clean_db")

TENANT = uuid.UUID("eeeeeeee-0000-4000-8000-000000000005")
OTHER = uuid.UUID("ffffffff-0000-4000-8000-000000000006")


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _admin(tenant=TENANT):
    return create_access_token(subject=uuid.uuid4(), tenant_id=tenant, role="admin")


async def _register(client, **over):
    body = {
        "name": "support-bot",
        "agent_type": "support",
        "allowed_models": ["gpt-4o"],
        "allowed_tools": ["search_kb"],
        "max_requests_per_minute": 30,
    }
    body.update(over)
    r = await client.post("/v1/agents", json=body, headers=_h(_admin()))
    assert r.status_code == 201, r.text
    return r.json()


def _agent_token(agent_id: str, tenant=TENANT):
    return create_access_token(
        subject=uuid.UUID(agent_id), tenant_id=tenant, role="agent", caller_type="agent"
    )


def _body(agent_id, tenant=TENANT, **over):
    body = {
        "request_id": str(uuid.uuid4()),
        "tenant_id": str(tenant),
        "caller_type": "agent",
        "caller_id": agent_id,
        "agent_id": agent_id,
        "requested_model": "gpt-4o",
        "requested_endpoint": "https://api.model-eu.example/v1/chat",
        "destination_jurisdiction": "eu",
        "declared_purpose": "support",
        "data_classification": "internal",
        "prompt": "Summarise the release notes in three bullets.",
    }
    body.update(over)
    return body


async def _infer(client, agent_id, **over):
    r = await client.post(
        "/v1/ai/infer", json=_body(agent_id, **over), headers=_h(_agent_token(agent_id))
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _seed_baseline(agent_id: str, days: int = 3, per_bucket: int = 2):
    """Office-hours history: a few requests per 10 minutes, one destination, one dataset."""
    now = datetime.now(UTC)
    async for s in get_session():
        for d in range(1, days + 1):
            for minute in range(0, 24 * 60, 10):
                for k in range(per_bucket):
                    s.add(
                        AgentEvent(
                            agent_id=uuid.UUID(agent_id),
                            tenant_id=TENANT,
                            request_id=uuid.uuid4(),
                            event_type="infer",
                            tokens_processed=40,
                            sensitive_fields_detected=0,
                            destination="api.model-eu.example",
                            latency_ms=20,
                            status="allow",
                            datasets=["kb"],
                            tool_calls=0,
                            content_hash=f"{d}-{minute}-{k}",
                            created_at=now - timedelta(days=d, minutes=minute),
                        )
                    )
        await s.commit()


async def test_register_and_read_agent(client):
    agent = await _register(client)
    assert agent["allowed_models"] == ["gpt-4o"] and agent["risk_score"] == 0
    read = await client.get(f"/v1/agents/{agent['id']}", headers=_h(_admin()))
    assert read.json()["name"] == "support-bot"
    listing = await client.get("/v1/agents", headers=_h(_admin()))
    assert [a["id"] for a in listing.json()] == [agent["id"]]


async def test_registration_requires_agent_manage(client):
    analyst = create_access_token(subject=uuid.uuid4(), tenant_id=TENANT, role="security_analyst")
    r = await client.post(
        "/v1/agents",
        json={"name": "x", "agent_type": "x", "allowed_models": ["m"]},
        headers=_h(analyst),
    )
    assert r.status_code == 403


async def test_agents_are_tenant_isolated(client):
    agent = await _register(client)
    other_admin = _admin(OTHER)
    assert (
        await client.get(f"/v1/agents/{agent['id']}", headers=_h(other_admin))
    ).status_code == 403
    assert (await client.get("/v1/agents", headers=_h(other_admin))).json() == []
    # Another tenant's token cannot drive this agent.
    r = await client.post(
        "/v1/ai/infer",
        json=_body(agent["id"], tenant=OTHER),
        headers=_h(_agent_token(agent["id"], tenant=OTHER)),
    )
    assert r.json()["error"]["code"] == "unknown_agent"


async def test_normal_agent_request_recorded_as_event(client):
    agent = await _register(client)
    body = await _infer(client, agent["id"])
    assert body["final_decision"] == "allow"
    assert body["agent"]["method"] == "insufficient_baseline" and not body["agent"]["is_anomalous"]
    [row] = await raw_sql(
        "select status, destination, decision_id, tenant_id from agent_events where agent_id = $1",
        uuid.UUID(agent["id"]),
    )
    assert row["status"] == "allow" and row["destination"] == "api.model-eu.example"
    assert str(row["decision_id"]) == body["decision_id"] and row["tenant_id"] == TENANT


async def test_unapproved_model_denied(client):
    agent = await _register(client)
    body = await _infer(client, agent["id"], requested_model="some-other-model")
    assert body["final_decision"] == "deny"
    assert "block_unapproved_agent_model@1.0.0" in body["matched_policies"]


async def test_unapproved_tool_denied_and_empty_tool_list_means_none(client):
    agent = await _register(client)
    ok = await _infer(
        client, agent["id"], tool_calls=[{"name": "search_kb", "arguments": {"q": "x"}}]
    )
    assert ok["final_decision"] == "allow"
    bad = await _infer(client, agent["id"], tool_calls=[{"name": "delete_records"}])
    assert "block_unapproved_agent_tool@1.0.0" in bad["matched_policies"]
    no_tools = await _register(client, name="no-tools", allowed_tools=[])
    r = await _infer(client, no_tools["id"], tool_calls=[{"name": "search_kb"}])
    assert r["final_decision"] == "deny"


async def test_new_destination_triggers_review(client):
    agent = await _register(client)
    await _seed_baseline(agent["id"])
    normal = await _infer(client, agent["id"])
    assert normal["agent"]["method"] == "isolation_forest+stats"
    assert not normal["agent"]["is_anomalous"], normal["agent"]
    odd = await _infer(
        client, agent["id"], requested_endpoint="https://upload.drop-share.example/x"
    )
    assert odd["agent"]["is_anomalous"]
    assert "new_external_destination" in odd["agent"]["reasons"]
    assert odd["final_decision"] == "review"
    assert "review_anomalous_agent@1.0.0" in odd["matched_policies"]


async def test_burst_is_rate_limited_per_agent(client):
    agent = await _register(client, max_requests_per_minute=3)
    codes = []
    for _ in range(5):
        r = await client.post(
            "/v1/ai/infer", json=_body(agent["id"]), headers=_h(_agent_token(agent["id"]))
        )
        codes.append(r.status_code)
    assert codes[:3] == [200, 200, 200] and 429 in codes[3:]


async def test_risk_endpoint_and_score_rises(client):
    agent = await _register(client)
    await _seed_baseline(agent["id"])
    for _ in range(3):
        await _infer(client, agent["id"], requested_endpoint="https://exfil.example/upload")
    risk = await client.get(f"/v1/agents/{agent['id']}/risk", headers=_h(_admin()))
    assert risk.status_code == 200
    body = risk.json()
    assert body["risk_score"] > 0.3
    assert (
        body["current"]["is_anomalous"] and "new_external_destination" in body["current"]["reasons"]
    )
    assert body["events_7d"] > 3 * 24 * 6
    listing = (await client.get("/v1/agents?min_risk=0.3", headers=_h(_admin()))).json()
    assert [a["id"] for a in listing] == [agent["id"]]


async def test_risk_requires_audit_read(client):
    agent = await _register(client)
    dev = create_access_token(subject=uuid.uuid4(), tenant_id=TENANT, role="developer")
    assert (await client.get(f"/v1/agents/{agent['id']}/risk", headers=_h(dev))).status_code == 403


async def test_agent_verdict_in_audit_trail(client):
    agent = await _register(client)
    body = await _infer(client, agent["id"])
    detail = (await client.get(f"/v1/decisions/{body['decision_id']}", headers=_h(_admin()))).json()
    ev = detail["audit_events"][0]["event_data"]
    assert ev["agent"]["agent_id"] == agent["id"]
    assert ev["agent"]["model_allowed"] is True and ev["agent"]["tool_allowed"] is True
    assert ev["model_versions"]["anomaly_detector"].startswith("agent-iforest")
    assert detail["agent_id"] == agent["id"]


async def test_user_acting_through_agent_gets_agent_controls(client):
    agent = await _register(client)
    user = create_access_token(subject=uuid.uuid4(), tenant_id=TENANT, role="developer")
    from gateway.core.security import decode_access_token

    sub = str(decode_access_token(user).subject)
    r = await client.post(
        "/v1/ai/infer",
        json=_body(agent["id"], caller_type="user", caller_id=sub, requested_model="not-allowed"),
        headers=_h(user),
    )
    assert r.json()["final_decision"] == "deny"
