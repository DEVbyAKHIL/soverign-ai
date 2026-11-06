"""Agent registry and behaviour-risk endpoints."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.anomaly_detector import AnomalyDetector
from agents.features import compute_features
from agents.profile import activity_summary, load_events, require_agent
from gateway.core.security import Principal, Scope, require_scopes
from gateway.db import get_session
from gateway.dependencies import get_anomaly_detector
from gateway.models import Agent
from gateway.schemas.requests import AgentCreateRequest
from gateway.schemas.responses import AgentAnomalyOut, AgentOut, AgentRiskOut

router = APIRouter(prefix="/v1/agents", tags=["agents"])


def _out(a: Agent) -> AgentOut:
    return AgentOut(
        id=a.id,
        name=a.name,
        agent_type=a.agent_type,
        owner_id=a.owner_id,
        allowed_models=list(a.allowed_models),
        allowed_tools=list(a.allowed_tools),
        max_requests_per_minute=a.max_requests_per_minute,
        risk_score=float(a.risk_score),
        created_at=a.created_at,
    )


@router.post("", response_model=AgentOut, status_code=201)
async def register_agent(
    body: AgentCreateRequest,
    principal: Principal = Depends(require_scopes(Scope.AGENT_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> AgentOut:
    agent = Agent(
        id=uuid.uuid4(),
        tenant_id=principal.tenant_id,
        owner_id=principal.subject,
        name=body.name,
        agent_type=body.agent_type,
        allowed_models=sorted(set(body.allowed_models)),
        allowed_tools=sorted(set(body.allowed_tools)),
        max_requests_per_minute=body.max_requests_per_minute,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return _out(agent)


@router.get("", response_model=list[AgentOut])
async def list_agents(
    min_risk: float = Query(default=0.0, ge=0, le=1),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_scopes(Scope.AUDIT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[AgentOut]:
    """Agents of the caller's tenant, riskiest first (dashboard feed)."""
    stmt = (
        select(Agent)
        .where(Agent.tenant_id == principal.tenant_id, Agent.risk_score >= min_risk)
        .order_by(Agent.risk_score.desc(), Agent.created_at)
        .limit(limit)
    )
    return [_out(a) for a in (await session.execute(stmt)).scalars()]


@router.get("/{agent_id}", response_model=AgentOut)
async def read_agent(
    agent_id: uuid.UUID,
    principal: Principal = Depends(require_scopes(Scope.AUDIT_READ)),
    session: AsyncSession = Depends(get_session),
) -> AgentOut:
    return _out(await require_agent(session, principal.tenant_id, agent_id))


@router.get("/{agent_id}/risk", response_model=AgentRiskOut)
async def agent_risk(
    agent_id: uuid.UUID,
    principal: Principal = Depends(require_scopes(Scope.AUDIT_READ)),
    session: AsyncSession = Depends(get_session),
    detector: AnomalyDetector = Depends(get_anomaly_detector),
) -> AgentRiskOut:
    agent = await require_agent(session, principal.tenant_id, agent_id)
    window, baseline = await load_events(session, agent.id, datetime.now(UTC))
    result = detector.evaluate(
        str(agent.id), compute_features(window, baseline), agent.max_requests_per_minute
    )
    summary = await activity_summary(session, agent.id)
    return AgentRiskOut(
        agent=_out(agent),
        risk_score=float(agent.risk_score),
        current=AgentAnomalyOut(**result.as_dict()),
        **summary,
    )
