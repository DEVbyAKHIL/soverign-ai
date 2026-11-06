"""AI request endpoints. Every request goes through the same decision pipeline."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from detection.pipeline import DetectionPipeline
from gateway.core.security import Principal, Scope, require_scopes
from gateway.db import get_session
from gateway.dependencies import get_detector, get_policy_set, rate_limited
from gateway.schemas.requests import AIRequest, EmbedRequest, InferRequest
from gateway.schemas.responses import DecisionResponse, DetectionOut
from gateway.services.decisions import decide
from policy.loader import PolicySet

router = APIRouter(prefix="/v1/ai", tags=["ai"])


async def _run(
    endpoint: str,
    body: AIRequest,
    principal: Principal,
    session: AsyncSession,
    policy_set: PolicySet,
    detector: DetectionPipeline,
) -> DecisionResponse:
    out = await decide(
        session,
        principal=principal,
        req=body,
        policy_set=policy_set,
        detector=detector,
        endpoint=endpoint,
    )
    result, decision, det = out.policy, out.decision, out.detection
    transformed = None
    if out.transformed_parts is not None:
        key = "input" if endpoint == "embed" else "prompt"
        transformed = (
            {"input": out.transformed_parts}
            if key == "input"
            else {
                "prompt": out.transformed_parts[0],
                "attached_content": out.transformed_parts[1:],
            }
        )
    return DecisionResponse(
        decision_id=decision.id,
        request_id=decision.request_id,
        final_decision=result.final_decision,
        action_taken=result.action_taken,
        risk_score=float(decision.input_risk_score),
        explanation=result.explanation,
        matched_policies=list(result.matched_policies),
        side_effects=list(result.side_effects),
        transforms=list(result.transforms),
        route_to=result.route_to,
        review_status=decision.review_status,
        policy_set_version=result.policy_set_version,
        data_classification=det.classification,
        detections=[
            DetectionOut(
                entity_type=e.entity_type,
                confidence=e.confidence,
                detector=e.detector,
                model_version=e.model_version,
                start_offset=e.start,
                end_offset=e.end,
                part=e.part,
                risk_level=e.risk_level,
            )
            for e in det.entities
        ],
        transformed_content=transformed,
        audit_event_id=out.audit_event_id,
    )


@router.post("/infer", response_model=DecisionResponse)
async def infer(
    body: InferRequest,
    principal: Principal = Depends(require_scopes(Scope.AI_INVOKE)),
    _rl: Principal = Depends(rate_limited),
    session: AsyncSession = Depends(get_session),
    policy_set: PolicySet = Depends(get_policy_set),
    detector: DetectionPipeline = Depends(get_detector),
) -> DecisionResponse:
    return await _run("infer", body, principal, session, policy_set, detector)


@router.post("/embed", response_model=DecisionResponse)
async def embed(
    body: EmbedRequest,
    principal: Principal = Depends(require_scopes(Scope.AI_INVOKE)),
    _rl: Principal = Depends(rate_limited),
    session: AsyncSession = Depends(get_session),
    policy_set: PolicySet = Depends(get_policy_set),
    detector: DetectionPipeline = Depends(get_detector),
) -> DecisionResponse:
    return await _run("embed", body, principal, session, policy_set, detector)
