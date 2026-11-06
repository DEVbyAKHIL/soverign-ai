"""FastAPI dependency wiring."""

from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from detection.pipeline import DetectionPipeline
from gateway.config import get_settings
from gateway.core.rate_limit import RateLimiter
from gateway.core.security import Principal, get_current_principal
from gateway.core.tenant import TenantScope
from gateway.db import get_session
from gateway.redis_client import get_redis
from policy.loader import PolicySet


async def get_tenant_scope(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> AsyncIterator[TenantScope]:
    yield TenantScope(session, principal.tenant_id)


async def rate_limited(principal: Principal = Depends(get_current_principal)) -> Principal:
    limiter = RateLimiter(get_redis(), get_settings().rate_limit_per_minute)
    await limiter.hit("tenant", str(principal.tenant_id), "caller", str(principal.subject))
    return principal


def get_policy_set(request: Request) -> PolicySet:
    return request.app.state.policy_set


def get_detector(request: Request) -> DetectionPipeline:
    return request.app.state.detector
