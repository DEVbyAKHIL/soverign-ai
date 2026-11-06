import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from audit.service import list_events
from gateway.core.security import Principal, Scope, require_scopes
from gateway.db import get_session
from gateway.models import Detection
from gateway.schemas.requests import ReviewDecisionRequest
from gateway.schemas.responses import AuditEventOut, DecisionDetail, DetectionOut
from gateway.services.decisions import get_decision, record_review

router = APIRouter(tags=["decisions"])


async def _detail(
    session: AsyncSession, tenant_id: uuid.UUID, decision_id: uuid.UUID
) -> DecisionDetail:
    d = await get_decision(session, tenant_id, decision_id)
    detections = (
        (await session.execute(select(Detection).where(Detection.decision_id == d.id)))
        .scalars()
        .all()
    )
    events = await list_events(session, tenant_id, decision_id=d.id, limit=200)
    return DecisionDetail(
        decision_id=d.id,
        request_id=d.request_id,
        tenant_id=d.tenant_id,
        caller_id=d.caller_id,
        agent_id=d.agent_id,
        destination_model=d.destination_model,
        destination_jurisdiction=d.destination_jurisdiction,
        declared_purpose=d.declared_purpose,
        input_risk_score=float(d.input_risk_score),
        agent_anomaly_score=float(d.agent_anomaly_score),
        final_decision=d.final_decision,
        action_taken=d.action_taken,
        policy_ids=d.policy_ids,
        policy_set_version=d.policy_set_version,
        model_versions=d.model_versions,
        explanation=d.explanation,
        review_status=d.review_status,
        created_at=d.created_at,
        detections=[
            DetectionOut(
                entity_type=x.entity_type,
                confidence=float(x.confidence),
                detector=x.detector,
                model_version=x.model_version,
                start_offset=x.start_offset,
                end_offset=x.end_offset,
                part=x.part,
                risk_level=x.risk_level,
            )
            for x in detections
        ],
        audit_events=[
            AuditEventOut.model_validate(e, from_attributes=True) for e in reversed(events)
        ],
    )


@router.get("/v1/decisions/{decision_id}", response_model=DecisionDetail)
async def read_decision(
    decision_id: uuid.UUID,
    principal: Principal = Depends(require_scopes(Scope.AUDIT_READ)),
    session: AsyncSession = Depends(get_session),
) -> DecisionDetail:
    return await _detail(session, principal.tenant_id, decision_id)


@router.post("/v1/reviews/{decision_id}/decision", response_model=DecisionDetail)
async def review_decision(
    decision_id: uuid.UUID,
    body: ReviewDecisionRequest,
    principal: Principal = Depends(require_scopes(Scope.REVIEW_DECIDE)),
    session: AsyncSession = Depends(get_session),
) -> DecisionDetail:
    await record_review(
        session,
        principal=principal,
        decision_id=decision_id,
        outcome=body.outcome,
        reason=body.reason,
    )
    return await _detail(session, principal.tenant_id, decision_id)
