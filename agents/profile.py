"""Agent registry, behavioural event storage and allow-list checks."""

import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.features import BASELINE, WINDOW, EventRow
from gateway.core.errors import GatewayError
from gateway.models import Agent, AgentEvent

BASELINE_ROW_CAP = 5000


class UnknownAgent(GatewayError):
    status_code = 403
    code = "unknown_agent"


async def get_agent(
    session: AsyncSession, tenant_id: uuid.UUID, agent_id: uuid.UUID
) -> Agent | None:
    stmt = select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def require_agent(session: AsyncSession, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
    agent = await get_agent(session, tenant_id, agent_id)
    if agent is None:  # unregistered or another tenant's agent: same answer, no oracle
        raise UnknownAgent("Agent is not registered for this tenant")
    return agent


def destination_of(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    return (parsed.netloc or parsed.path or endpoint).lower()


def dataset_of(source: str) -> str:
    """Group documents into datasets: URL host, or the path without its last segment."""
    parsed = urlparse(source)
    if parsed.scheme and parsed.netloc:
        return parsed.netloc.lower()
    return source.rsplit("/", 1)[0] if "/" in source else source


def model_allowed(agent: Agent, model: str) -> bool:
    return model in agent.allowed_models


def tools_allowed(agent: Agent, tools: list[str]) -> bool:
    """Least privilege: an agent with no allowed tools may call none."""
    return set(tools) <= set(agent.allowed_tools)


def _row(e: AgentEvent) -> EventRow:
    return EventRow(
        created_at=e.created_at,
        tokens=e.tokens_processed,
        sensitive=e.sensitive_fields_detected,
        destination=e.destination,
        status=e.status,
        datasets=tuple(e.datasets or ()),
        tool_calls=e.tool_calls,
        content_hash=e.content_hash,
    )


async def load_events(
    session: AsyncSession, agent_id: uuid.UUID, now: datetime | None = None
) -> tuple[list[EventRow], list[EventRow]]:
    """Return (current window, baseline) event rows for an agent."""
    now = now or datetime.now(UTC)
    window_start, baseline_start = now - WINDOW, now - BASELINE
    window = (
        await session.execute(
            select(AgentEvent)
            .where(AgentEvent.agent_id == agent_id, AgentEvent.created_at >= window_start)
            .order_by(AgentEvent.created_at)
        )
    ).scalars()
    baseline = (
        await session.execute(
            select(AgentEvent)
            .where(
                AgentEvent.agent_id == agent_id,
                AgentEvent.created_at >= baseline_start,
                AgentEvent.created_at < window_start,
            )
            .order_by(AgentEvent.created_at.desc())
            .limit(BASELINE_ROW_CAP)
        )
    ).scalars()
    return [_row(e) for e in window], [_row(e) for e in baseline]


async def activity_summary(session: AsyncSession, agent_id: uuid.UUID) -> dict:
    since = datetime.now(UTC) - BASELINE
    row = (
        await session.execute(
            select(
                func.count(AgentEvent.id),
                func.count(AgentEvent.id).filter(AgentEvent.status == "deny"),
                func.coalesce(func.sum(AgentEvent.sensitive_fields_detected), 0),
                func.max(AgentEvent.created_at),
            ).where(AgentEvent.agent_id == agent_id, AgentEvent.created_at >= since)
        )
    ).one()
    return {
        "events_7d": int(row[0]),
        "denied_7d": int(row[1]),
        "sensitive_fields_7d": int(row[2]),
        "last_seen": row[3],
    }
