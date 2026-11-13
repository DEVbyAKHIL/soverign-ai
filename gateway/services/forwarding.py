"""Forward an approved decision to the model backend and guard the answer (Phase 6).

Runs after the decision is committed, so the decision stands whatever the backend does. Only
`allow` / `allow_with_modification` decisions that need no human review are forwarded, and only
the transformed content. The answer passes the output guard before the caller sees it, and a
`model.response` audit event records the outcome (hashes and verdicts, never raw text).
"""

from prometheus_client import Counter, Histogram
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from audit.service import append_event
from detection.output.guardrails import OutputGuard
from gateway.config import get_settings
from gateway.core.fingerprint import fingerprint
from gateway.core.security import Principal
from gateway.schemas.requests import AIRequest
from gateway.services.decisions import DecisionOutcome, payload
from gateway.services.model_client import ModelCall, ModelClient

FORWARDED = ("allow", "allow_with_modification")
MODEL_CALLS = Counter("gateway_model_calls_total", "Model backend calls", ["endpoint", "status"])
OUTPUT_ACTIONS = Counter("gateway_output_guard_total", "Output guard verdicts", ["action"])
MODEL_LATENCY = Histogram(
    "gateway_model_seconds",
    "Model backend latency",
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)


def skip_reason(out: DecisionOutcome, client: ModelClient | None) -> str | None:
    if out.policy.final_decision not in FORWARDED:
        return "not_approved"
    if out.decision.review_status == "pending":
        return "pending_review"
    if client is None:
        return "backend_disabled"
    return None


def _fingerprint(text: str) -> str:
    """Keyed HMAC of the model output: correlatable, but not confirmable by guessing the text."""
    return fingerprint(get_settings().fingerprint_hmac_key, "output", text)


async def forward(
    session: AsyncSession,
    *,
    principal: Principal,
    req: AIRequest,
    out: DecisionOutcome,
    endpoint: str,
    client: ModelClient | None,
    guard: OutputGuard,
) -> dict:
    """Return the `model_response` body. Never raises for backend failures."""
    reason = skip_reason(out, client)
    if reason or client is None:
        return {"status": "not_forwarded", "reason": reason}
    parts = out.transformed_parts or payload(req)[0]
    call: ModelCall = await (client.embed(parts) if endpoint == "embed" else client.chat(parts))
    MODEL_CALLS.labels(endpoint, call.status).inc()
    if call.status == "ok":
        MODEL_LATENCY.observe(call.latency_ms / 1000)

    body: dict = {"status": call.status, "model": call.model, "latency_ms": call.latency_ms}
    audit: dict = {
        **body,
        "endpoint": endpoint,
        "forwarded_transformed": bool(out.transformed_parts),
    }
    if call.status != "ok":
        body["error"] = audit["error"] = call.error
    elif call.embeddings is not None:
        body["embeddings"] = call.embeddings
        audit["dimensions"] = len(call.embeddings[0]) if call.embeddings else 0
    else:
        text = call.text or ""
        verdict = await run_in_threadpool(guard.check, text, out.detection.classification)
        OUTPUT_ACTIONS.labels(verdict.action).inc()
        body.update(text=verdict.text, usage=call.usage, output_guard=verdict.as_dict())
        audit.update(
            usage=call.usage,
            output_hmac=_fingerprint(text),
            output_chars=len(text),
            output_guard=verdict.as_dict(),
        )
        if verdict.action == "blocked":
            body["status"] = audit["status"] = "blocked"

    await append_event(
        session,
        tenant_id=principal.tenant_id,
        decision_id=out.decision.id,
        event_type="model.response",
        actor_id=principal.subject,
        event_data=audit,
    )
    await session.commit()
    return body
