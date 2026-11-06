"""Read-only aggregates for the dashboard (Phase 7). Tenant-scoped; requires audit:read.

Only counts, labels and scores are returned, never content: the audit trail holds none.
"""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.security import Principal, Scope, require_scopes
from gateway.db import get_session
from gateway.models import Agent, AuditEvent, Decision, Detection

router = APIRouter(tags=["dashboard"])


class DayPoint(BaseModel):
    day: str
    allow: int = 0
    allow_with_modification: int = 0
    deny: int = 0
    review: int = 0
    avg_risk: float = 0.0


class AgentRisk(BaseModel):
    agent_id: uuid.UUID
    name: str
    risk_score: float
    agent_type: str


class DashboardSummary(BaseModel):
    days: int
    total: int
    by_decision: dict[str, int]
    pending_reviews: int
    avg_risk: float
    timeline: list[DayPoint]
    top_policies: dict[str, int]
    entity_types: dict[str, int]
    intents: dict[str, int]
    model_responses: dict[str, int]  # ok | blocked | error
    output_guard: dict[str, int]  # pass | redacted | blocked
    agents: list[AgentRisk]


class DecisionRow(BaseModel):
    decision_id: uuid.UUID
    created_at: datetime
    final_decision: str
    action_taken: str
    risk_score: float
    intent_label: str
    destination_model: str
    destination_jurisdiction: str
    review_status: str
    policy_ids: list[str]
    agent_id: uuid.UUID | None


async def _counts(session: AsyncSession, column, *where) -> dict[str, int]:
    rows = await session.execute(select(column, func.count()).where(*where).group_by(column))
    return {str(k): int(n) for k, n in rows.all() if k is not None}


@router.get("/v1/dashboard/summary", response_model=DashboardSummary)
async def summary(
    days: int = Query(default=7, ge=1, le=90),
    principal: Principal = Depends(require_scopes(Scope.AUDIT_READ)),
    session: AsyncSession = Depends(get_session),
) -> DashboardSummary:
    tenant = principal.tenant_id
    since = datetime.now(UTC) - timedelta(days=days)
    scope = (Decision.tenant_id == tenant, Decision.created_at >= since)

    by_decision = await _counts(session, Decision.final_decision, *scope)
    pending = await session.scalar(
        select(func.count()).where(*scope, Decision.review_status == "pending")
    )
    avg_risk = await session.scalar(select(func.avg(Decision.input_risk_score)).where(*scope))

    day = cast(Decision.created_at, Date)
    rows = await session.execute(
        select(day, Decision.final_decision, func.count(), func.avg(Decision.input_risk_score))
        .where(*scope)
        .group_by(day, Decision.final_decision)
        .order_by(day)
    )
    points: dict[str, DayPoint] = {}
    weighted: dict[str, tuple[float, int]] = {}
    for d, decision, n, risk in rows.all():
        p = points.setdefault(str(d), DayPoint(day=str(d)))
        if decision in DayPoint.model_fields:
            setattr(p, decision, int(n))
        total_risk, count = weighted.get(str(d), (0.0, 0))
        weighted[str(d)] = (total_risk + float(risk or 0) * n, count + n)
    for k, (total_risk, count) in weighted.items():
        points[k].avg_risk = round(total_risk / count, 4) if count else 0.0

    policy = func.unnest(Decision.policy_ids).label("policy")
    sub = select(policy).where(*scope).subquery()
    top = await session.execute(
        select(sub.c.policy, func.count()).group_by(sub.c.policy).order_by(func.count().desc())
    )
    top_policies = {str(k): int(n) for k, n in top.all()[:8]}

    entities = await _counts(
        session,
        Detection.entity_type,
        Detection.decision_id.in_(select(Decision.id).where(*scope)),
    )
    intents = await _counts(session, Decision.intent_label, *scope)

    event_scope = (
        AuditEvent.tenant_id == tenant,
        AuditEvent.created_at >= since,
        AuditEvent.event_type == "model.response",
    )
    model_responses = await _counts(session, AuditEvent.event_data["status"].astext, *event_scope)
    output_guard = await _counts(
        session, AuditEvent.event_data["output_guard"]["action"].astext, *event_scope
    )

    agents = (
        await session.execute(
            select(Agent)
            .where(Agent.tenant_id == tenant)
            .order_by(Agent.risk_score.desc())
            .limit(8)
        )
    ).scalars()

    return DashboardSummary(
        days=days,
        total=sum(by_decision.values()),
        by_decision=by_decision,
        pending_reviews=int(pending or 0),
        avg_risk=round(float(avg_risk or 0), 4),
        timeline=list(points.values()),
        top_policies=top_policies,
        entity_types=entities,
        intents=intents,
        model_responses=model_responses,
        output_guard=output_guard,
        agents=[
            AgentRisk(
                agent_id=a.id, name=a.name, risk_score=float(a.risk_score), agent_type=a.agent_type
            )
            for a in agents
        ],
    )


@router.get("/v1/decisions", response_model=list[DecisionRow])
async def list_decisions(
    review_status: str | None = Query(default=None, pattern=r"^[a-z_]+$", max_length=32),
    final_decision: str | None = Query(default=None, pattern=r"^[a-z_]+$", max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_scopes(Scope.AUDIT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[DecisionRow]:
    stmt = select(Decision).where(Decision.tenant_id == principal.tenant_id)
    if review_status:
        stmt = stmt.where(Decision.review_status == review_status)
    if final_decision:
        stmt = stmt.where(Decision.final_decision == final_decision)
    rows = (await session.execute(stmt.order_by(Decision.created_at.desc()).limit(limit))).scalars()
    return [
        DecisionRow(
            decision_id=d.id,
            created_at=d.created_at,
            final_decision=d.final_decision,
            action_taken=d.action_taken,
            risk_score=float(d.input_risk_score),
            intent_label=d.intent_label,
            destination_model=d.destination_model,
            destination_jurisdiction=d.destination_jurisdiction,
            review_status=d.review_status,
            policy_ids=list(d.policy_ids),
            agent_id=d.agent_id,
        )
        for d in rows
    ]
