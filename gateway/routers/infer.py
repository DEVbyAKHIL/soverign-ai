"""AI request endpoints. Inference, embedding and RAG share one decision pipeline."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from detection.injection.detector import InjectionDetector
from detection.pipeline import DetectionPipeline
from gateway.core.security import Principal, Scope, require_scopes
from gateway.db import get_session
from gateway.dependencies import get_detector, get_injection_detector, get_policy_set, rate_limited
from gateway.schemas.requests import AIRequest, EmbedRequest, InferRequest, RagQueryRequest
from gateway.schemas.responses import ContentVerdict, DecisionResponse, DetectionOut, IntentOut
from gateway.services.decisions import DecisionOutcome, decide
from policy.loader import PolicySet

router = APIRouter(tags=["ai"])


def _transformed(endpoint: str, body: AIRequest, parts: list[str] | None) -> dict | None:
    if parts is None:
        return None
    if endpoint == "embed":
        return {"input": parts}
    if endpoint == "rag" and isinstance(body, RagQueryRequest):
        return {
            "query": parts[0],
            "documents": [
                {"doc_id": d.doc_id, "content": c}
                for d, c in zip(body.documents, parts[1:], strict=True)
            ],
        }
    return {"prompt": parts[0], "attached_content": parts[1:]}


def _response(endpoint: str, body: AIRequest, out: DecisionOutcome) -> DecisionResponse:
    result, decision, det = out.policy, out.decision, out.detection
    doc_ids = [d.doc_id for d in body.documents] if isinstance(body, RagQueryRequest) else []
    direct = out.direct_intent
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
        intent=IntentOut(
            label=direct.label,
            confidence=direct.confidence,
            risk_score=direct.risk_score,
            signals=list(direct.signals),
        ),
        content_verdicts=[
            ContentVerdict(
                part=i.part,
                source=i.source,
                label=i.label,
                risk_score=i.risk_score,
                signals=list(i.signals),
                doc_id=doc_ids[i.part - 1] if endpoint == "rag" and i.part > 0 else None,
            )
            for i in out.intents
        ],
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
        transformed_content=_transformed(endpoint, body, out.transformed_parts),
        audit_event_id=out.audit_event_id,
    )


async def _run(
    endpoint: str,
    body: AIRequest,
    principal: Principal,
    session: AsyncSession,
    policy_set: PolicySet,
    detector: DetectionPipeline,
    injection: InjectionDetector,
) -> DecisionResponse:
    out = await decide(
        session,
        principal=principal,
        req=body,
        policy_set=policy_set,
        detector=detector,
        injection=injection,
        endpoint=endpoint,
    )
    return _response(endpoint, body, out)


@router.post("/v1/ai/infer", response_model=DecisionResponse)
async def infer(
    body: InferRequest,
    principal: Principal = Depends(require_scopes(Scope.AI_INVOKE)),
    _rl: Principal = Depends(rate_limited),
    session: AsyncSession = Depends(get_session),
    policy_set: PolicySet = Depends(get_policy_set),
    detector: DetectionPipeline = Depends(get_detector),
    injection: InjectionDetector = Depends(get_injection_detector),
) -> DecisionResponse:
    return await _run("infer", body, principal, session, policy_set, detector, injection)


@router.post("/v1/ai/embed", response_model=DecisionResponse)
async def embed(
    body: EmbedRequest,
    principal: Principal = Depends(require_scopes(Scope.AI_INVOKE)),
    _rl: Principal = Depends(rate_limited),
    session: AsyncSession = Depends(get_session),
    policy_set: PolicySet = Depends(get_policy_set),
    detector: DetectionPipeline = Depends(get_detector),
    injection: InjectionDetector = Depends(get_injection_detector),
) -> DecisionResponse:
    return await _run("embed", body, principal, session, policy_set, detector, injection)


@router.post("/v1/rag/query", response_model=DecisionResponse)
async def rag_query(
    body: RagQueryRequest,
    principal: Principal = Depends(require_scopes(Scope.AI_INVOKE)),
    _rl: Principal = Depends(rate_limited),
    session: AsyncSession = Depends(get_session),
    policy_set: PolicySet = Depends(get_policy_set),
    detector: DetectionPipeline = Depends(get_detector),
    injection: InjectionDetector = Depends(get_injection_detector),
) -> DecisionResponse:
    """Inspect a RAG query and its retrieved documents before they reach a model.

    Each document gets its own verdict in `content_verdicts` (with `doc_id`), so a caller whose
    request is quarantined can see which documents were poisoned.
    """
    return await _run("rag", body, principal, session, policy_set, detector, injection)
