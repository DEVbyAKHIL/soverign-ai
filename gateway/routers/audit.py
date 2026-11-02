import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from audit.service import list_events, verify_tenant_chain
from gateway.core.security import Principal, Scope, require_scopes
from gateway.db import get_session
from gateway.schemas.responses import AuditEventOut, ChainVerificationOut

router = APIRouter(prefix="/v1/audit", tags=["audit"])


@router.get("/events", response_model=list[AuditEventOut])
async def audit_events(
    decision_id: uuid.UUID | None = None,
    event_type: str | None = Query(default=None, max_length=64, pattern=r"^[a-z_.]+$"),
    before_seq: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_scopes(Scope.AUDIT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[AuditEventOut]:
    events = await list_events(
        session,
        principal.tenant_id,
        decision_id=decision_id,
        event_type=event_type,
        before_seq=before_seq,
        limit=limit,
    )
    return [AuditEventOut.model_validate(e, from_attributes=True) for e in events]


@router.get("/verify", response_model=ChainVerificationOut)
async def verify(
    principal: Principal = Depends(require_scopes(Scope.AUDIT_READ)),
    session: AsyncSession = Depends(get_session),
) -> ChainVerificationOut:
    result = await verify_tenant_chain(session, principal.tenant_id)
    return ChainVerificationOut(**result.__dict__)
