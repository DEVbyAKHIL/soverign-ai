"""End-to-end decision flows against a real PostgreSQL (requires TEST_DATABASE_URL)."""

import uuid

import asyncpg
import pytest

from tests.conftest import _exec, raw_sql

pytestmark = pytest.mark.usefixtures("clean_db")

TENANT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
OTHER_TENANT = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")


def _token(make_token, role="admin", tenant=TENANT, subject=None, caller_type="user"):
    from gateway.core.security import create_access_token

    return create_access_token(
        subject=subject or uuid.uuid4(), tenant_id=tenant, role=role, caller_type=caller_type
    )


def _identity(token: str):
    from gateway.core.security import decode_access_token

    p = decode_access_token(token)
    return p.subject, p.tenant_id


def _body(token: str, **over) -> dict:
    sub, tenant = _identity(token)
    body = {
        "request_id": str(uuid.uuid4()),
        "tenant_id": str(tenant),
        "caller_type": "user",
        "caller_id": str(sub),
        "requested_model": "gpt-4o",
        "requested_endpoint": "https://api.example.com/v1/chat",
        "destination_jurisdiction": "eu",
        "declared_purpose": "customer_support_summarisation",
        "data_classification": "internal",
        "prompt": "Summarise this synthetic ticket for Jane Example.",
    }
    body.update(over)
    return body


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _infer(client, token, **over):
    return await client.post("/v1/ai/infer", json=_body(token, **over), headers=_h(token))


async def test_allow_flow(client, make_token):
    t = _token(make_token)
    r = await _infer(client, t)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["final_decision"] == "allow"
    assert body["review_status"] == "not_required"
    assert body["model_response"] is None

    detail = (await client.get(f"/v1/decisions/{body['decision_id']}", headers=_h(t))).json()
    assert detail["final_decision"] == "allow"
    assert detail["policy_ids"] == ["builtin.default_allow"]
    [event] = detail["audit_events"]
    assert event["id"] == body["audit_event_id"]
    assert event["event_type"] == "decision.created"
    assert event["previous_hash"] == "0" * 64


async def test_deny_flow_matches_spec_example(client, make_token):
    t = _token(make_token)
    r = await _infer(
        client, t, destination_jurisdiction="non_eu", data_classification="confidential"
    )
    body = r.json()
    assert body["final_decision"] == "deny"
    assert body["action_taken"] == "deny"
    assert body["matched_policies"] == ["block_pii_to_non_eu_models@1.2.0"]
    assert body["review_status"] == "pending"  # create_review_case
    assert set(body["side_effects"]) == {"alert", "create_review_case"}
    assert "non_eu" in body["explanation"]


async def test_route_flow(client, make_token):
    t = _token(make_token)
    body = (
        await _infer(
            client, t, destination_jurisdiction="unknown", data_classification="confidential"
        )
    ).json()
    assert body["action_taken"] == "route_to_approved_model"
    assert body["route_to"] == {"model": "eu-approved-llm", "jurisdiction": "eu"}


async def test_unlabelled_data_defaults_to_confidential(client, make_token):
    t = _token(make_token)
    b = _body(t, destination_jurisdiction="non_eu")
    del b["data_classification"]
    r = await client.post("/v1/ai/infer", json=b, headers=_h(t))
    assert r.json()["final_decision"] == "deny"


async def test_embed_uses_same_pipeline(client, make_token):
    t = _token(make_token)
    b = _body(t, destination_jurisdiction="non_eu", data_classification="restricted")
    del b["prompt"]
    b["input"] = ["synthetic text"]
    r = await client.post("/v1/ai/embed", json=b, headers=_h(t))
    assert r.status_code == 200
    assert r.json()["final_decision"] == "deny"


async def test_raw_prompt_never_stored(client, make_token):
    t = _token(make_token)
    secret = "SYNTHETIC-SECRET-" + uuid.uuid4().hex
    await _infer(client, t, prompt=f"remember {secret}")
    rows = await raw_sql(
        "select event_data::text as d from audit_events union all select explanation from decisions"
    )
    assert rows and all(secret not in r["d"] for r in rows)
    [ev] = await raw_sql("select event_data->'payload' as p from audit_events")
    assert '"content_sha256"' in ev["p"]


async def test_human_review_flow(client, make_token):
    requester = _token(make_token, role="developer")
    body = (await _infer(client, requester, data_classification="high_risk")).json()
    assert (body["final_decision"], body["review_status"]) == ("review", "pending")
    did = body["decision_id"]

    reviewer = _token(make_token, role="security_analyst")
    r = await client.post(
        f"/v1/reviews/{did}/decision",
        json={"outcome": "approve", "reason": "Synthetic data, approved for test"},
        headers=_h(reviewer),
    )
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["review_status"] == "approved"
    assert [e["event_type"] for e in detail["audit_events"]] == [
        "decision.created",
        "review.decided",
    ]
    assert detail["audit_events"][1]["event_data"]["outcome"] == "approve"

    again = await client.post(
        f"/v1/reviews/{did}/decision",
        json={"outcome": "reject", "reason": "second opinion"},
        headers=_h(reviewer),
    )
    assert again.status_code == 409


async def test_review_requires_scope_and_pending_status(client, make_token):
    t = _token(make_token)
    did = (await _infer(client, t)).json()["decision_id"]  # allow -> not reviewable
    dev = _token(make_token, role="developer")
    body = {"outcome": "approve", "reason": "nope"}
    assert (
        await client.post(f"/v1/reviews/{did}/decision", json=body, headers=_h(dev))
    ).status_code == 403
    analyst = _token(make_token, role="security_analyst")
    assert (
        await client.post(f"/v1/reviews/{did}/decision", json=body, headers=_h(analyst))
    ).status_code == 409


async def test_reviewer_cannot_approve_own_request(client, make_token):
    me = uuid.uuid4()
    admin = _token(make_token, role="admin", subject=me)
    did = (await _infer(client, admin, data_classification="high_risk")).json()["decision_id"]
    r = await client.post(
        f"/v1/reviews/{did}/decision",
        json={"outcome": "approve", "reason": "self approval"},
        headers=_h(admin),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "separation_of_duties"


async def test_duplicate_request_id_rejected(client, make_token):
    t = _token(make_token)
    b = _body(t)
    assert (await client.post("/v1/ai/infer", json=b, headers=_h(t))).status_code == 200
    r = await client.post("/v1/ai/infer", json=b, headers=_h(t))
    assert r.status_code == 409


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", str(OTHER_TENANT)),
        ("caller_id", str(uuid.uuid4())),
        ("caller_type", "service"),
    ],
)
async def test_cannot_act_as_someone_else(client, make_token, field, value):
    t = _token(make_token)
    r = await _infer(client, t, **{field: value})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "identity_mismatch"


async def test_agent_requires_agent_id(client, make_token):
    t = _token(make_token, role="agent", caller_type="agent")
    r = await _infer(client, t, caller_type="agent")
    assert r.status_code == 422
    r = await _infer(client, t, caller_type="agent", agent_id=str(uuid.uuid4()))
    assert r.status_code == 200


async def test_tenant_isolation_on_reads(client, make_token):
    a = _token(make_token, tenant=TENANT)
    b = _token(make_token, tenant=OTHER_TENANT)
    did = (await _infer(client, a)).json()["decision_id"]

    assert (await client.get(f"/v1/decisions/{did}", headers=_h(b))).status_code == 404
    assert (await client.get("/v1/audit/events", headers=_h(b))).json() == []
    assert (await client.get(f"/v1/audit/events?decision_id={did}", headers=_h(b))).json() == []
    review = await client.post(
        f"/v1/reviews/{did}/decision",
        json={"outcome": "approve", "reason": "x" * 5},
        headers=_h(b),
    )
    assert review.status_code == 404


async def test_audit_reads_require_scope(client, make_token):
    dev = _token(make_token, role="developer")
    assert (await client.get("/v1/audit/events", headers=_h(dev))).status_code == 403
    assert (await client.get("/v1/audit/verify", headers=_h(dev))).status_code == 403
    assert (await client.get(f"/v1/decisions/{uuid.uuid4()}", headers=_h(dev))).status_code == 403


async def test_audit_chain_links_and_verifies(client, make_token):
    t = _token(make_token)
    for _ in range(5):
        await _infer(client, t)
    events = (await client.get("/v1/audit/events?limit=10", headers=_h(t))).json()
    assert len(events) == 5
    ordered = sorted(events, key=lambda e: e["seq"])
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        assert cur["previous_hash"] == prev["event_hash"]

    v = (await client.get("/v1/audit/verify", headers=_h(t))).json()
    assert v == {"valid": True, "events_checked": 5, "first_invalid_seq": None, "reason": None}


async def test_chains_are_per_tenant(client, make_token):
    a = _token(make_token, tenant=TENANT)
    b = _token(make_token, tenant=OTHER_TENANT)
    await _infer(client, a)
    await _infer(client, b)
    for t in (a, b):
        [ev] = (await client.get("/v1/audit/events", headers=_h(t))).json()
        assert ev["previous_hash"] == "0" * 64


async def test_audit_pagination(client, make_token):
    t = _token(make_token)
    for _ in range(4):
        await _infer(client, t)
    page1 = (await client.get("/v1/audit/events?limit=2", headers=_h(t))).json()
    page2 = (
        await client.get(f"/v1/audit/events?limit=2&before_seq={page1[-1]['seq']}", headers=_h(t))
    ).json()
    assert len(page1) == len(page2) == 2
    assert {e["id"] for e in page1}.isdisjoint({e["id"] for e in page2})


async def test_audit_table_is_append_only(client, make_token):
    t = _token(make_token)
    await _infer(client, t)
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await raw_sql("update audit_events set event_type = 'tampered'")
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await raw_sql("delete from audit_events")


async def test_tampering_is_detected(client, make_token):
    """Simulate a privileged attacker who disables the trigger and edits history."""
    t = _token(make_token)
    for _ in range(3):
        await _infer(client, t)
    await _exec(
        "alter table audit_events disable trigger audit_events_no_update_delete;"
        "update audit_events set event_data = jsonb_set(event_data, '{final_decision}', "
        "'\"deny\"') where seq = (select min(seq) + 1 from audit_events);"
        "alter table audit_events enable trigger audit_events_no_update_delete;"
    )
    v = (await client.get("/v1/audit/verify", headers=_h(t))).json()
    assert v["valid"] is False
    assert v["reason"] == "event_hash does not match content"


async def test_decision_records_policy_and_model_versions(client, make_token):
    t = _token(make_token)
    body = (await _infer(client, t)).json()
    detail = (await client.get(f"/v1/decisions/{body['decision_id']}", headers=_h(t))).json()
    assert detail["policy_set_version"] == body["policy_set_version"]
    assert detail["model_versions"]["policy_set"] == body["policy_set_version"]
    event_data = detail["audit_events"][0]["event_data"]
    assert len(event_data["policies_evaluated"]) == 4
    assert event_data["redaction_map"] is None


async def test_decision_metrics_exported(client, make_token):
    t = _token(make_token)
    await _infer(client, t, destination_jurisdiction="non_eu", data_classification="restricted")
    text = (await client.get("/metrics")).text
    assert 'gateway_decisions_total{action="deny",endpoint="infer",final_decision="deny"}' in text
    assert "gateway_policy_alerts_total" in text
