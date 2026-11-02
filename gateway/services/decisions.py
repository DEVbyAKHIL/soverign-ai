"""Decision pipeline: context -> policy evaluation -> decision + audit in one transaction."""

import hashlib
import uuid
from decimal import Decimal

from prometheus_client import Counter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from audit.service import append_event
from gateway.core.errors import GatewayError, TenantIsolationError
from gateway.core.security import Principal
from gateway.logging_config import get_logger
from gateway.models import Decision
from gateway.schemas.requests import AIRequest, EmbedRequest, InferRequest
from policy.engine import EvaluationContext, PolicyDecision, evaluate
from policy.loader import PolicySet

log = get_logger(__name__)

DECISIONS = Counter(
    "gateway_decisions_total", "Policy decisions", ["endpoint", "final_decision", "action"]
)
POLICY_ALERTS = Counter("gateway_policy_alerts_total", "Policy alerts raised", ["policy"])

# Phase 2 has no detectors yet; versions are recorded so later phases slot in.
MODEL_VERSIONS = {"pii_detector": None, "injection_classifier": None, "anomaly_detector": None}
SENSITIVE_CLASSES = {"confidential", "restricted", "high_risk"}


class IdentityMismatch(GatewayError):
    status_code = 403
    code = "identity_mismatch"


class DuplicateRequest(GatewayError):
    status_code = 409
    code = "duplicate_request"


class ReviewConflict(GatewayError):
    status_code = 409
    code = "review_conflict"


class SeparationOfDuties(GatewayError):
    status_code = 403
    code = "separation_of_duties"


def check_identity(principal: Principal, req: AIRequest) -> None:
    """The body must describe the authenticated caller; it cannot speak for someone else."""
    if (
        req.tenant_id != principal.tenant_id
        or req.caller_id != principal.subject
        or req.caller_type != principal.caller_type
    ):
        raise IdentityMismatch("Request identity does not match the authenticated caller")


def build_context(req: AIRequest) -> EvaluationContext:
    sensitivity = {req.data_classification} & SENSITIVE_CLASSES
    return EvaluationContext(
        caller_type=req.caller_type,
        destination_jurisdiction=req.destination_jurisdiction,
        data_classification=req.data_classification,
        requested_model=req.requested_model,
        declared_purpose=req.declared_purpose,
        data_sensitivity=frozenset(sensitivity),
    )


def _content_fingerprint(req: AIRequest) -> dict:
    """Minimal, non-reversible description of the payload. Raw content is never stored."""
    if isinstance(req, InferRequest):
        parts = [req.prompt, *req.attached_content]
    elif isinstance(req, EmbedRequest):
        parts = list(req.input)
    else:  # pragma: no cover
        parts = []
    digest = hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()
    return {
        "content_sha256": digest,
        "content_chars": sum(len(p) for p in parts),
        "parts": len(parts),
    }


async def decide(
    session: AsyncSession,
    *,
    principal: Principal,
    req: AIRequest,
    policy_set: PolicySet,
    endpoint: str,
) -> tuple[Decision, PolicyDecision, uuid.UUID]:
    check_identity(principal, req)
    ctx = build_context(req)
    result = evaluate(ctx, policy_set)
    review_status = "pending" if result.requires_review else "not_required"

    decision = Decision(
        id=uuid.uuid4(),
        request_id=req.request_id,
        tenant_id=principal.tenant_id,
        caller_id=principal.subject,
        agent_id=req.agent_id,
        destination_model=req.requested_model,
        destination_jurisdiction=req.destination_jurisdiction,
        declared_purpose=req.declared_purpose,
        input_risk_score=Decimal(str(ctx.risk_score)),
        agent_anomaly_score=Decimal(str(ctx.agent_anomaly_score)),
        final_decision=result.final_decision,
        action_taken=result.action_taken,
        policy_ids=list(result.matched_policies) or [result.decisive_policy],
        model_versions={**MODEL_VERSIONS, "policy_set": policy_set.version},
        explanation=result.explanation,
        policy_set_version=policy_set.version,
        review_status=review_status,
    )
    session.add(decision)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateRequest("request_id has already been processed for this tenant") from exc

    event = await append_event(
        session,
        tenant_id=principal.tenant_id,
        decision_id=decision.id,
        event_type="decision.created",
        actor_id=principal.subject,
        event_data={
            "endpoint": endpoint,
            "request_id": str(req.request_id),
            "caller_type": req.caller_type,
            "agent_id": str(req.agent_id) if req.agent_id else None,
            "requested_model": req.requested_model,
            "requested_endpoint": req.requested_endpoint,
            "destination_jurisdiction": req.destination_jurisdiction,
            "declared_purpose": req.declared_purpose,
            "data_classification": req.data_classification,
            "payload": _content_fingerprint(req),
            "detections": [],
            "risk_score": ctx.risk_score,
            "agent_anomaly_score": ctx.agent_anomaly_score,
            "policies_evaluated": [t["policy"] for t in result.trace],
            "matched_policies": list(result.matched_policies),
            "decisive_policy": result.decisive_policy,
            "actions": list(result.actions),
            "final_decision": result.final_decision,
            "action_taken": result.action_taken,
            "route_to": result.route_to,
            "policy_set_version": policy_set.version,
            "model_versions": decision.model_versions,
            "review_status": review_status,
            "redaction_map": None,
            "explanation": result.explanation,
        },
    )
    await session.commit()

    DECISIONS.labels(endpoint, result.final_decision, result.action_taken).inc()
    if "alert" in result.side_effects:
        for ref in result.matched_policies:
            POLICY_ALERTS.labels(ref).inc()
        log.warning(
            "policy_alert",
            decision_id=str(decision.id),
            tenant_id=str(principal.tenant_id),
            action=result.action_taken,
            policies=list(result.matched_policies),
        )
    return decision, result, event.id


async def get_decision(
    session: AsyncSession, tenant_id: uuid.UUID, decision_id: uuid.UUID
) -> Decision:
    stmt = select(Decision).where(Decision.id == decision_id, Decision.tenant_id == tenant_id)
    decision = (await session.execute(stmt)).scalar_one_or_none()
    if decision is None:
        raise TenantIsolationError("Decision not found")
    return decision


async def record_review(
    session: AsyncSession,
    *,
    principal: Principal,
    decision_id: uuid.UUID,
    outcome: str,
    reason: str,
) -> tuple[Decision, uuid.UUID]:
    # Lock the row so two reviewers cannot both decide the same case.
    stmt = (
        select(Decision)
        .where(Decision.id == decision_id, Decision.tenant_id == principal.tenant_id)
        .with_for_update()
    )
    decision = (await session.execute(stmt)).scalar_one_or_none()
    if decision is None:
        raise TenantIsolationError("Decision not found")
    if decision.review_status != "pending":
        raise ReviewConflict(f"Decision is not awaiting review (status: {decision.review_status})")
    if decision.caller_id == principal.subject:
        raise SeparationOfDuties("Reviewers cannot decide on their own requests")

    decision.review_status = "approved" if outcome == "approve" else "rejected"
    event = await append_event(
        session,
        tenant_id=principal.tenant_id,
        decision_id=decision.id,
        event_type="review.decided",
        actor_id=principal.subject,
        event_data={
            "outcome": outcome,
            "reason": reason,
            "reviewer_role": principal.role,
            "previous_status": "pending",
            "new_status": decision.review_status,
        },
    )
    await session.commit()
    log.info("review_decided", decision_id=str(decision.id), outcome=outcome)
    return decision, event.id
