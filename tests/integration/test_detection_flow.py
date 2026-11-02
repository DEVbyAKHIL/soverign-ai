"""Phase 3 end-to-end: detection -> policy -> transforms -> persistence (needs a test DB)."""

import uuid

import pytest

from gateway.core.security import create_access_token, decode_access_token
from tests.conftest import raw_sql

pytestmark = pytest.mark.usefixtures("clean_db")

TENANT = uuid.UUID("cccccccc-0000-4000-8000-000000000003")


def _token(role="developer"):
    return create_access_token(subject=uuid.uuid4(), tenant_id=TENANT, role=role)


def _body(token, **over):
    p = decode_access_token(token)
    body = {
        "request_id": str(uuid.uuid4()),
        "tenant_id": str(p.tenant_id),
        "caller_type": "user",
        "caller_id": str(p.subject),
        "requested_model": "gpt-4o",
        "requested_endpoint": "https://api.example.com/v1/chat",
        "destination_jurisdiction": "eu",
        "declared_purpose": "customer_support_summarisation",
        "data_classification": "internal",
        "prompt": "Summarise the release notes.",
    }
    body.update(over)
    return body


async def _infer(client, token, **over):
    r = await client.post(
        "/v1/ai/infer", json=_body(token, **over), headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_spec_example_denied_with_detections(client):
    """The API contract example: personal + financial data to a non-EU endpoint."""
    body = await _infer(
        client,
        _token(),
        destination_jurisdiction="non_eu",
        prompt="Summarise this customer ticket: John Müller, email john@example.com, "
        "balance €12,400.",
    )
    assert body["final_decision"] == "deny"
    assert "block_pii_to_non_eu_models@1.2.0" in body["matched_policies"]
    types = {d["entity_type"] for d in body["detections"]}
    assert {"EMAIL", "FINANCIAL"} <= types
    assert body["risk_score"] > 0.7
    assert body["data_classification"] == "confidential"  # raised from declared "internal"
    assert body["transformed_content"] is None  # denied requests are not transformed


async def test_pii_to_eu_is_pseudonymised(client):
    body = await _infer(client, _token(), prompt="Reply to jane.doe@example.com about the order.")
    assert body["final_decision"] == "allow_with_modification"
    assert body["action_taken"] == "pseudonymise"
    out = body["transformed_content"]["prompt"]
    assert "jane.doe@example.com" not in out and "<EMAIL_" in out


async def test_secrets_are_redacted_everywhere(client):
    key = "sk-proj-" + "A1b2C3d4E5" * 4
    body = await _infer(
        client,
        _token(),
        prompt="Debug this call",
        attached_content=[f"config: api_key={key}"],
    )
    assert body["action_taken"] == "redact"
    assert "redact_secrets@1.0.0" in body["matched_policies"]
    attached = body["transformed_content"]["attached_content"][0]
    assert key not in attached and "[REDACTED:API_KEY]" in attached
    assert body["detections"][0]["part"] == 1


async def test_card_tokenised_for_eu_model(client):
    # Card in a request to an EU model: pseudonymise (scope=all pii) outranks mask, so tokenised.
    body = await _infer(client, _token(), prompt="Refund card 4111 1111 1111 1111 please.")
    assert body["final_decision"] == "allow_with_modification"
    out = body["transformed_content"]["prompt"]
    assert "4111 1111 1111 1111" not in out and out.endswith("1111 please.")


async def test_medical_data_goes_to_review(client):
    body = await _infer(client, _token(), prompt="The patient was diagnosed with epilepsy.")
    assert (body["final_decision"], body["review_status"]) == ("review", "pending")
    assert body["data_classification"] == "high_risk"


async def test_clean_prompt_still_allowed(client):
    body = await _infer(client, _token(), prompt="Summarise the release notes.")
    assert body["final_decision"] == "allow" and body["detections"] == []


async def test_detections_persisted_hashed_and_raw_values_never_stored(client):
    secret_email = f"unique.{uuid.uuid4().hex[:8]}@example.com"
    card = "5555 5555 5555 4444"
    body = await _infer(
        client, _token(), prompt=f"Contact {secret_email}, card {card}, password: Zz9!qwerty"
    )
    rows = await raw_sql(
        "select entity_type, entity_value_hash, part, risk_level from detections "
        "where decision_id = $1",
        uuid.UUID(body["decision_id"]),
    )
    assert {r["entity_type"] for r in rows} >= {"EMAIL", "CREDIT_CARD", "PASSWORD"}
    assert all(len(r["entity_value_hash"]) == 64 for r in rows)

    dump = await raw_sql(
        "select string_agg(t, ' ') as all_text from ("
        " select event_data::text as t from audit_events union all"
        " select entity_value_hash from detections union all"
        " select explanation from decisions) s"
    )
    text = dump[0]["all_text"]
    for raw in (secret_email, card, card.replace(" ", ""), "Zz9!qwerty"):
        assert raw not in text


async def test_audit_event_explains_detection(client):
    t = _token("admin")
    body = await _infer(client, t, prompt="Email bob@example.org", data_classification="public")
    detail = (
        await client.get(
            f"/v1/decisions/{body['decision_id']}", headers={"Authorization": f"Bearer {t}"}
        )
    ).json()
    ev = detail["audit_events"][0]["event_data"]
    assert ev["declared_classification"] == "public"
    assert ev["effective_classification"] == "confidential"
    assert ev["detections"][0]["entity_type"] == "EMAIL"
    assert ev["redaction_map"][0]["transform"] == "pseudonymise"
    assert "bob@example.org" not in str(ev)
    assert ev["model_versions"]["regex_detector"].startswith("regex-")
    assert detail["detections"][0]["risk_level"] == "medium"


async def test_pseudonyms_stable_within_tenant(client):
    t = _token()
    a = await _infer(client, t, prompt="Write to ana@example.com")
    b = await _infer(client, t, prompt="Write to ana@example.com")
    assert a["transformed_content"] == b["transformed_content"]
