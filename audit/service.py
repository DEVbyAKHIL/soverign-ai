"""Append-only audit service with per-tenant hash chains."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from audit.hash_chain import (
    GENESIS_HASH,
    ChainVerification,
    compute_hash,
    hashed_fields,
    verify_chain,
)
from gateway.models import AuditChainAnchor, AuditEvent


async def append_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    event_type: str,
    actor_id: uuid.UUID,
    event_data: dict[str, Any],
) -> AuditEvent:
    """Append one event in the caller's transaction.

    A transaction-scoped advisory lock serialises appends per tenant so two
    concurrent writers can never fork the chain. The caller commits.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"audit_chain:{tenant_id}"},
    )
    previous = (
        await session.execute(
            select(AuditEvent.event_hash)
            .where(AuditEvent.tenant_id == tenant_id)
            .order_by(AuditEvent.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none() or GENESIS_HASH

    event_id = uuid.uuid4()
    # Python and Postgres both use microsecond precision, so this round-trips exactly.
    created_at = datetime.now(UTC)
    event = AuditEvent(
        id=event_id,
        tenant_id=tenant_id,
        decision_id=decision_id,
        event_type=event_type,
        actor_id=actor_id,
        event_data=event_data,
        previous_hash=previous,
        created_at=created_at,
        event_hash=compute_hash(
            previous,
            hashed_fields(
                event_id=event_id,
                tenant_id=tenant_id,
                decision_id=decision_id,
                event_type=event_type,
                actor_id=actor_id,
                event_data=event_data,
                created_at=created_at,
            ),
        ),
    )
    session.add(event)
    await session.flush()
    return event


async def list_events(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    decision_id: uuid.UUID | None = None,
    event_type: str | None = None,
    before_seq: int | None = None,
    limit: int = 50,
) -> list[AuditEvent]:
    stmt = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
    if decision_id:
        stmt = stmt.where(AuditEvent.decision_id == decision_id)
    if event_type:
        stmt = stmt.where(AuditEvent.event_type == event_type)
    if before_seq:
        stmt = stmt.where(AuditEvent.seq < before_seq)
    stmt = stmt.order_by(AuditEvent.seq.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def verify_tenant_chain(session: AsyncSession, tenant_id: uuid.UUID) -> ChainVerification:
    """Verify the retained part of the tenant's chain, starting from the retention anchor if the
    oldest events were pruned (ops/retention.py), otherwise from the genesis hash."""
    anchor = await session.scalar(
        select(AuditChainAnchor.anchor_hash).where(AuditChainAnchor.tenant_id == tenant_id)
    )
    stmt = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.seq)
    events = (await session.execute(stmt)).scalars().all()
    return verify_chain(events, anchor or GENESIS_HASH)
