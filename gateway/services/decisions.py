"""Decision pipeline: detect -> policy -> transform -> decision + audit, in one transaction."""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from prometheus_client import Counter, Histogram
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from agents.anomaly_detector import AnomalyDetector, AnomalyResult
from agents.features import EventRow, compute_features
from agents.profile import (
    dataset_of,
    destination_of,
    load_events,
    model_allowed,
    require_agent,
    tools_allowed,
)
from agents.risk_score import updated_risk
from audit.service import append_event
from detection.injection.detector import InjectionDetector, IntentResult, worst
from detection.pipeline import DetectionPipeline, DetectionResult
from detection.transforms import TransformPlan, apply_plan, entity_hash
from detection.types import max_classification
from gateway.config import get_settings
from gateway.core.errors import GatewayError, TenantIsolationError
from gateway.core.rate_limit import RateLimiter
from gateway.core.security import Principal
from gateway.logging_config import get_logger
from gateway.models import Agent, AgentEvent, Decision, Detection
from gateway.redis_client import get_redis
from gateway.schemas.requests import AIRequest, EmbedRequest, InferRequest, RagQueryRequest
from policy.engine import EvaluationContext, PolicyDecision, evaluate
from policy.loader import PolicySet

log = get_logger(__name__)

DECISIONS = Counter(
    "gateway_decisions_total", "Policy decisions", ["endpoint", "final_decision", "action"]
)
POLICY_ALERTS = Counter("gateway_policy_alerts_total", "Policy alerts raised", ["policy"])
DETECTIONS = Counter("gateway_detections_total", "Sensitive entities detected", ["entity_type"])
INTENTS = Counter("gateway_intent_total", "Intent verdicts per content part", ["label", "source"])
AGENT_ANOMALIES = Counter("gateway_agent_anomalies_total", "Anomalous agent requests", ["reason"])
DETECTION_LATENCY = Histogram(
    "gateway_detection_seconds",
    "Sensitive-data detection latency",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.15, 0.25, 0.5, 1.0),
)


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
    intents: tuple[IntentResult, ...]  # one per content part
    agent_anomaly: AnomalyResult | None = None

    @property
    def direct_intent(self) -> IntentResult:
        return worst([i for i in self.intents if i.source == "direct"]) or self.intents[0]


def check_identity(principal: Principal, req: AIRequest) -> None:
    """The body must describe the authenticated caller; it cannot speak for someone else."""
    if (
        req.tenant_id != principal.tenant_id
        or req.caller_id != principal.subject
        or req.caller_type != principal.caller_type
    ):
        raise IdentityMismatch("Request identity does not match the authenticated caller")
    if req.caller_type == "agent" and req.agent_id != principal.subject:
        raise IdentityMismatch("An agent can only act as itself")


def payload(req: AIRequest) -> tuple[list[str], list[str]]:
    """Content parts and their source. The caller's own words are "direct"; anything the caller
    merely passes along (attachments, retrieved documents, text to embed) is "indirect"."""
    if isinstance(req, InferRequest):
        attached = list(req.attached_content)
        return [req.prompt, *attached], ["direct"] + ["indirect"] * len(attached)
    if isinstance(req, RagQueryRequest):
        docs = [d.content for d in req.documents]
        return [req.query, *docs], ["direct"] + ["indirect"] * len(docs)
    if isinstance(req, EmbedRequest):
        return list(req.input), ["indirect"] * len(req.input)
    return [], []  # pragma: no cover


def declared_classification(req: AIRequest) -> str:
    if isinstance(req, RagQueryRequest):
        labels = [d.classification for d in req.documents if d.classification]
        return max_classification(req.data_classification, *labels)
    return req.data_classification


def combined_risk(det: DetectionResult, intents: list[IntentResult]) -> float:
    """Noisy-OR of sensitive-data risk and the highest injection risk."""
    injection = max((i.risk_score for i in intents), default=0.0)
    return round(1 - (1 - det.risk_score) * (1 - injection), 4)


def build_context(
    req: AIRequest,
    det: DetectionResult,
    intents: list[IntentResult],
    agent: Agent | None = None,
    anomaly: AnomalyResult | None = None,
) -> EvaluationContext:
    direct = worst([i for i in intents if i.source == "direct"])
    indirect = worst([i for i in intents if i.source == "indirect"])
    return EvaluationContext(
        caller_type=req.caller_type,
        destination_jurisdiction=req.destination_jurisdiction,
        data_classification=det.classification,
        requested_model=req.requested_model,
        declared_purpose=req.declared_purpose,
        data_sensitivity=det.sensitivity,
        entity_types=det.entity_types,
        intent_label=direct.label if direct else "benign",
        indirect_intent_label=indirect.label if indirect else "benign",
        risk_score=combined_risk(det, intents),
        agent_anomaly_score=anomaly.anomaly_score if anomaly else 0.0,
        agent_model_allowed=model_allowed(agent, req.requested_model) if agent else True,
        agent_tool_allowed=tools_allowed(agent, [t.name for t in req.tool_calls])
        if agent
        else True,
    )


def datasets_of(req: AIRequest) -> tuple[str, ...]:
    if isinstance(req, RagQueryRequest):
        return tuple(sorted({d.dataset or dataset_of(d.source) for d in req.documents}))
    return ()


async def agent_behaviour(
    session: AsyncSession,
    anomaly_detector: AnomalyDetector,
    agent: Agent,
    current: EventRow,
) -> AnomalyResult:
    """Score the agent's behaviour including the request being decided."""
    window, baseline = await load_events(session, agent.id, current.created_at)
    features = compute_features([*window, current], baseline)
    return anomaly_detector.evaluate(str(agent.id), features, agent.max_requests_per_minute)


def _analyze(
    detector: DetectionPipeline,
    injection: InjectionDetector,
    parts: list[str],
    sources: list[str],
    declared: str,
) -> tuple[DetectionResult, list[IntentResult]]:
    det = detector.analyze(parts, declared)
    intents = [
        injection.analyze(text, source, i)
        for i, (text, source) in enumerate(zip(parts, sources, strict=True))
    ]
    return det, intents


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
    injection: InjectionDetector,
    anomaly_detector: AnomalyDetector,
    endpoint: str,
) -> DecisionOutcome:
    check_identity(principal, req)
    settings = get_settings()
    started = datetime.now(UTC)
    agent = (
        await require_agent(session, principal.tenant_id, req.agent_id) if req.agent_id else None
    )
    if agent is not None:
        # Per-agent limit on top of the per-caller limit applied by the router.
        await RateLimiter(get_redis(), agent.max_requests_per_minute).hit("agent", str(agent.id))
    parts, sources = payload(req)

    # CPU-bound (regex, NER, classifiers, optional LLM review); keep it off the event loop.
    det, intents = await run_in_threadpool(
        _analyze, detector, injection, parts, sources, declared_classification(req)
    )
    DETECTION_LATENCY.observe(det.latency_ms / 1000)
    fingerprint = _fingerprint(parts)
    current: EventRow | None = None
    anomaly: AnomalyResult | None = None
    if agent is not None:
        current = EventRow(
            created_at=started,
            tokens=max(1, fingerprint["content_chars"] // 4),
            sensitive=len(det.entities),
            destination=destination_of(req.requested_endpoint),
            status="pending",
            datasets=datasets_of(req),
            tool_calls=len(req.tool_calls),
            content_hash=fingerprint["content_sha256"],
        )
        anomaly = await agent_behaviour(session, anomaly_detector, agent, current)
    ctx = build_context(req, det, intents, agent, anomaly)
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
        **injection.model_versions,
        "anomaly_detector": anomaly_detector.version,
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
        intent_label=ctx.intent_label,
        indirect_intent_label=ctx.indirect_intent_label,
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
            "payload": fingerprint,
            "tool_calls": [t.name for t in req.tool_calls],
            "agent": (
                {
                    **anomaly.as_dict(),
                    "model_allowed": ctx.agent_model_allowed,
                    "tool_allowed": ctx.agent_tool_allowed,
                }
                if anomaly
                else None
            ),
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
            "intent": {
                "direct": ctx.intent_label,
                "indirect": ctx.indirect_intent_label,
                "parts": [i.as_dict() for i in intents],
            },
            "documents": (
                [{"doc_id": d.doc_id, "source": d.source} for d in req.documents]
                if isinstance(req, RagQueryRequest)
                else None
            ),
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
    if agent is not None and current is not None and anomaly is not None:
        session.add(
            AgentEvent(
                agent_id=agent.id,
                tenant_id=principal.tenant_id,
                decision_id=decision.id,
                request_id=req.request_id,
                event_type=endpoint,
                tokens_processed=current.tokens,
                sensitive_fields_detected=current.sensitive,
                destination=current.destination,
                latency_ms=int((datetime.now(UTC) - started).total_seconds() * 1000),
                status=result.final_decision,
                datasets=list(current.datasets),
                tool_calls=current.tool_calls,
                content_hash=current.content_hash,
                created_at=started,
            )
        )
        agent.risk_score = Decimal(
            str(updated_risk(float(agent.risk_score), anomaly.anomaly_score))
        )
        agent.updated_at = datetime.now(UTC)
    await session.commit()

    DECISIONS.labels(endpoint, result.final_decision, result.action_taken).inc()
    if anomaly and anomaly.is_anomalous:
        for reason in anomaly.reasons:
            AGENT_ANOMALIES.labels(reason).inc()
    for e in det.entities:
        DETECTIONS.labels(e.entity_type).inc()
    for i in intents:
        INTENTS.labels(i.label, i.source).inc()
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
            intent=ctx.intent_label,
            indirect_intent=ctx.indirect_intent_label,
            agent_anomaly=list(anomaly.reasons) if anomaly and anomaly.is_anomalous else None,
        )
    return DecisionOutcome(decision, result, det, event.id, transformed, tuple(intents), anomaly)


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
