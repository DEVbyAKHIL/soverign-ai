"""Auth endpoints. Phase 1: a development-only token issuer and a whoami endpoint.

Production deployments obtain tokens from an external OIDC provider instead.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from gateway.config import get_settings
from gateway.core.errors import GatewayError
from gateway.core.security import ROLE_SCOPES, Principal, create_access_token, get_current_principal

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class DevTokenRequest(BaseModel):
    subject: uuid.UUID
    tenant_id: uuid.UUID
    role: str
    caller_type: Literal["user", "service", "agent"] = "user"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 (OAuth2 token type, not a secret)
    expires_in: int


class NotFound(GatewayError):
    status_code = 404
    code = "not_found"


class InvalidRole(GatewayError):
    status_code = 422
    code = "validation_error"


@router.post("/dev-token", response_model=TokenResponse)
async def dev_token(body: DevTokenRequest) -> TokenResponse:
    settings = get_settings()
    if not settings.is_development:
        raise NotFound("Not found")
    if body.role not in ROLE_SCOPES:
        raise InvalidRole(f"Unknown role; expected one of: {', '.join(sorted(ROLE_SCOPES))}")
    token = create_access_token(
        subject=body.subject,
        tenant_id=body.tenant_id,
        role=body.role,
        caller_type=body.caller_type,
    )
    return TokenResponse(access_token=token, expires_in=settings.access_token_ttl_seconds)


@router.get("/me")
async def me(principal: Principal = Depends(get_current_principal)) -> dict:
    return {
        "subject": str(principal.subject),
        "tenant_id": str(principal.tenant_id),
        "caller_type": principal.caller_type,
        "role": principal.role,
        "scopes": sorted(principal.scopes),
    }
