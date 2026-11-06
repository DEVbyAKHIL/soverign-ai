"""Phase 7: dashboard aggregates and decision list (needs a test DB)."""

import uuid

import httpx
import pytest

from gateway.core.security import create_access_token
from gateway.services.model_client import ModelClient
from tests.integration.test_forwarding_flow import FakeBackend, _post

pytestmark = pytest.mark.usefixtures("clean_db")

TENANT = uuid.UUID("cccccccc-0000-4000-8000-000000000006")  # same tenant as _post


def _h(role="security_analyst", tenant=TENANT):
    token = create_access_token(subject=uuid.uuid4(), tenant_id=tenant, role=role)
    return {"Authorization": f"Bearer {token}"}


async def _seed(client, app):
    fake = FakeBackend("Contact jane.doe@example.com for details.")
    app.state.model_client = ModelClient("http://model/v1", transport=httpx.MockTransport(fake))
    await _post(client, "/v1/ai/infer")  # allow, output redacted
    await _post(
        client,
        "/v1/ai/infer",
        destination_jurisdiction="non_eu",
        prompt="Summarise: customer email john@example.com, balance €12,400.",
    )  # deny


async def test_summary_counts(client, app):
    await _seed(client, app)
    r = await client.get("/v1/dashboard/summary?days=7", headers=_h())
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["total"] == 2
    assert s["by_decision"] == {"allow": 1, "deny": 1}
    assert s["entity_types"]["EMAIL"] == 1
    assert s["model_responses"] == {"ok": 1}
    assert s["output_guard"] == {"redacted": 1}
    assert "block_pii_to_non_eu_models@1.2.0" in s["top_policies"]
    [day] = s["timeline"]
    assert (day["allow"], day["deny"]) == (1, 1)
    assert 0 < s["avg_risk"] < 1


async def test_summary_is_tenant_scoped_and_needs_audit_read(client, app):
    await _seed(client, app)
    other = await client.get("/v1/dashboard/summary", headers=_h(tenant=uuid.uuid4()))
    assert other.json()["total"] == 0
    assert (await client.get("/v1/dashboard/summary", headers=_h("developer"))).status_code == 403


async def test_list_decisions_filters(client, app):
    await _seed(client, app)
    rows = (await client.get("/v1/decisions", headers=_h())).json()
    assert len(rows) == 2
    assert rows[0]["created_at"] >= rows[1]["created_at"]
    denied = (await client.get("/v1/decisions?final_decision=deny", headers=_h())).json()
    assert [d["final_decision"] for d in denied] == ["deny"]
    assert (await client.get("/v1/decisions?review_status=x;drop", headers=_h())).status_code == 422
