"""Decision pipeline: detect -> policy -> transform -> decision + audit, in one transaction."""

import hashlib
import uuid
from dataclasses import dataclass
from decimal import Decimal

from prometheus_client import Counter, Histogram
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from audit.service import append_event
from detection.pipeline import DetectionPipeline, DetectionResult
from detection.transforms import TransformPlan, apply_plan, entity_hash
from gateway.config import get_settings
from gateway.core.errors import GatewayError, TenantIsolationError
from gateway.core.security import Principal
from gateway.logging_config import get_logger
from gateway.models import Decision, Detection
from gateway.schemas.requests import AIRequest, EmbedRequest, InferRequest
from policy.engine import EvaluationContext, PolicyDecision, evaluate
from policy.loader import PolicySet

log = get_logger(__name__)

DECISIONS = Counter(
    "gateway_decisions_total", "Policy decisions", ["endpoint", "final_decision", "action"]
)
POLICY_ALERTS = Counter("gateway_policy_alerts_total", "Policy alerts raised", ["policy"])
DETECTIONS = Counter("gateway_detections_total", "Sensitive entities detected", ["entity_type"])
DETECTION_LATENCY = Histogram(
    "gateway_detection_seconds",
    "Sensitive-data detection latency",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.15, 0.25, 0.5, 1.0),
)

# Filled in by later phases.
FUTURE_MODELS = {"injection_classifier": None, "anomaly_detector": None}


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


@dataclass(frozen=True)
class DecisionOutcome:
    decision: Decision
    policy: PolicyDecision
    detection: DetectionResult
    audit_event_id: uuid.UUID
    # Content after redaction/masking/pseudonymisation; None if nothing was transformed.
    transformed_parts: list[str] | None


def check_identity(principal: Principal, req: AIRequest) -> None:
    """The body must describe the authenticated caller; it cannot speak for someone else."""
    if (
        req.tenant_id != principal.tenant_id
        or req.caller_id != principal.subject
        or req.caller_type != principal.caller_type
    ):
        raise IdentityMismatch("Request identity does not match the authenticated caller")


def payload_parts(req: AIRequest) -> list[str]:
    if isinstance(req, InferRequest):
        return [req.prompt, *req.attached_content]
    if isinstance(req, EmbedRequest):
        return list(req.input)
    return []  # pragma: no cover


def build_context(req: AIRequest, det: DetectionResult) -> EvaluationContext:
    return EvaluationContext(
        caller_type=req.caller_type,
        destination_jurisdiction=req.destination_jurisdiction,
        data_classification=det.classification,
        requested_model=req.requested_model,
        declared_purpose=req.declared_purpose,
        data_sensitivity=det.sensitivity,
        entity_types=det.entity_types,
        risk_score=det.risk_score,
    )


def _fingerprint(parts: list[str]) -> dict:
    """Minimal, non-reversible description of the payload. Raw content is never stored."""
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
    detector: DetectionPipeline,
    endpoint: str,
) -> DecisionOutcome:
    check_identity(principal, req)
    settings = get_settings()
    parts = payload_parts(req)

    # CPU-bound (regex + NER); keep it off the event loop.
    det = await run_in_threadpool(detector.analyze, parts, req.data_classification)
    DETECTION_LATENCY.observe(det.latency_ms / 1000)
    ctx = build_context(req, det)
    result = evaluate(ctx, policy_set)
    review_status = "pending" if result.requires_review else "not_required"

    transformed: list[str] | None = None
    redaction_map: list[dict] = []
    if result.transform_plan:
        transformed, redaction_map = apply_plan(
            parts,
            det.entities,
            TransformPlan(result.transform_plan),
            master_key=settings.entity_hash_key,
            tenant_id=principal.tenant_id,
        )
        if not redaction_map:
            transformed = None

    model_versions = {
        **det.model_versions,
        **FUTURE_MODELS,
        "policy_set": policy_set.version,
    }
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
        model_versions=model_versions,
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

    for e in det.entities:
        session.add(
            Detection(
                decision_id=decision.id,
                entity_type=e.entity_type,
                entity_value_hash=entity_hash(
                    settings.entity_hash_key, principal.tenant_id, e.entity_type, e.value
                ),
                confidence=Decimal(str(e.confidence)),
                detector=e.detector,
                model_version=e.model_version,
                start_offset=e.start,
                end_offset=e.end,
                part=e.part,
                risk_level=e.risk_level,
            )
        )

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
            "declared_classification": req.data_classification,
            "effective_classification": det.classification,
            "payload": _fingerprint(parts),
            "detections": [
                {
                    "entity_type": e.entity_type,
                    "confidence": e.confidence,
                    "detector": e.detector,
                    "model_version": e.model_version,
                    "part": e.part,
                    "start": e.start,
                    "end": e.end,
                    "risk_level": e.risk_level,
                }
                for e in det.entities
            ],
            "data_sensitivity": sorted(det.sensitivity),
            "sensitivity_classifier": (
                {"label": det.classifier.label, "confidence": det.classifier.confidence}
                if det.classifier
                else None
            ),
            "detection_signals": list(det.signals),
            "detection_latency_ms": det.latency_ms,
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
            "model_versions": model_versions,
            "review_status": review_status,
            "redaction_map": redaction_map or None,
            "explanation": result.explanation,
        },
    )
    await session.commit()

    DECISIONS.labels(endpoint, result.final_decision, result.action_taken).inc()
    for e in det.entities:
        DETECTIONS.labels(e.entity_type).inc()
    if "alert" in result.side_effects:
        for ref in result.matched_policies:
            POLICY_ALERTS.labels(ref).inc()
        log.warning(
            "policy_alert",
            decision_id=str(decision.id),
            tenant_id=str(principal.tenant_id),
            action=result.action_taken,
            policies=list(result.matched_policies),
            entity_types=sorted(det.entity_types),
        )
    return DecisionOutcome(decision, result, det, event.id, transformed)


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
