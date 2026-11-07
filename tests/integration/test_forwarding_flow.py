"""Phase 6 end-to-end: decision -> forward to model -> output guard -> audit (needs a test DB)."""

import json
import uuid

import httpx
import pytest

from gateway.core.security import create_access_token, decode_access_token
from gateway.services.model_client import ModelClient
from tests.conftest import raw_sql

pytestmark = pytest.mark.usefixtures("clean_db")

TENANT = uuid.UUID("cccccccc-0000-4000-8000-000000000006")


class FakeBackend:
    """Records what the gateway sends and answers with a scripted reply."""

    def __init__(self, reply: str = "Here is the summary.") -> None:
        self.reply, self.requests = reply, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        self.requests.append(body)
        if req.url.path.endswith("/embeddings"):
            return httpx.Response(
                200, json={"data": [{"embedding": [0.5, 0.5]} for _ in body["input"]]}
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": self.reply}}]})


@pytest.fixture
def backend(app):
    fake = FakeBackend()
    app.state.model_client = ModelClient("http://model/v1", transport=httpx.MockTransport(fake))
    return fake


def _token():
    return create_access_token(subject=uuid.uuid4(), tenant_id=TENANT, role="developer")


async def _post(client, path, **over):
    token = _token()
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
    if path != "/v1/ai/infer":
        del body["prompt"]  # embed / rag requests carry input / query instead
    body.update(over)
    r = await client.post(path, json=body, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()


async def _model_events():
    return await raw_sql(
        "select event_data::text as d from audit_events where event_type = 'model.response'"
    )


async def test_allowed_request_is_forwarded_and_audited(client, backend):
    body = await _post(client, "/v1/ai/infer")
    mr = body["model_response"]
    assert mr["status"] == "ok"
    assert mr["text"] == "Here is the summary."
    assert mr["output_guard"]["action"] == "pass"
    assert backend.requests[0]["messages"][1]["content"] == "Summarise the release notes."
    [ev] = await _model_events()
    data = json.loads(ev["d"])
    assert data["status"] == "ok" and len(data["output_hmac"]) == 64
    assert "Here is the summary" not in ev["d"]  # the answer itself is never audited


async def test_only_transformed_content_reaches_the_model(client, backend):
    body = await _post(
        client,
        "/v1/ai/infer",
        prompt="Summarise the ticket from jane.doe@example.com about the late delivery.",
    )
    assert body["final_decision"] == "allow_with_modification"
    sent = backend.requests[0]["messages"][1]["content"]
    assert "jane.doe@example.com" not in sent
    assert body["model_response"]["status"] == "ok"


async def test_pii_in_answer_is_redacted(client, backend):
    backend.reply = "Contact the customer at jane.doe@example.com."
    mr = (await _post(client, "/v1/ai/infer"))["model_response"]
    assert mr["status"] == "ok"
    assert mr["output_guard"]["action"] == "redacted"
    assert "jane.doe@example.com" not in mr["text"]


async def test_secret_in_answer_is_blocked(client, backend):
    backend.reply = "Sure, the key is sk-proj-AbCdEfGhIjKlMnOpQrStUvWx123456"
    mr = (await _post(client, "/v1/ai/infer"))["model_response"]
    assert mr["status"] == "blocked"
    assert mr["text"] is None
    [ev] = await _model_events()
    assert json.loads(ev["d"])["status"] == "blocked"
    assert "sk-proj" not in ev["d"]


async def test_denied_request_is_never_forwarded(client, backend):
    body = await _post(
        client,
        "/v1/ai/infer",
        destination_jurisdiction="non_eu",
        prompt="Summarise: customer email john@example.com, balance €12,400.",
    )
    assert body["final_decision"] == "deny"
    assert body["model_response"] == {"status": "not_forwarded", "reason": "not_approved"}
    assert backend.requests == []
    assert await _model_events() == []


async def test_backend_failure_is_reported_not_raised(client, app):
    def down(req):
        raise httpx.ConnectError("refused")

    app.state.model_client = ModelClient("http://model/v1", transport=httpx.MockTransport(down))
    mr = (await _post(client, "/v1/ai/infer"))["model_response"]
    assert mr == {
        "status": "error",
        "model": mr["model"],
        "latency_ms": 0.0,
        "error": "ConnectError",
    }
    [ev] = await _model_events()
    assert json.loads(ev["d"])["error"] == "ConnectError"


async def test_embed_and_rag_are_forwarded(client, backend):
    emb = await _post(client, "/v1/ai/embed", input=["release notes", "roadmap"])
    assert emb["model_response"]["embeddings"] == [[0.5, 0.5], [0.5, 0.5]]
    rag = await _post(
        client,
        "/v1/rag/query",
        query="What changed in the release?",
        documents=[{"doc_id": "kb-1", "source": "kb", "content": "Search is faster."}],
    )
    assert rag["model_response"]["status"] == "ok"
    sent = backend.requests[-1]["messages"][1]["content"]
    assert "<<<DOCUMENT 1>>>\nSearch is faster." in sent
