"""JWT authentication and scope-based authorisation (OAuth2/OIDC-ready)."""

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from gateway.config import Settings, get_settings
from gateway.core.errors import AuthenticationError, AuthorisationError


class Scope(StrEnum):
    AI_INVOKE = "ai:invoke"
    POLICY_READ = "policy:read"
    POLICY_WRITE = "policy:write"
    AUDIT_READ = "audit:read"
    REVIEW_DECIDE = "review:decide"
    AGENT_MANAGE = "agent:manage"


ROLE_SCOPES: dict[str, frozenset[Scope]] = {
    "admin": frozenset(Scope),
    "security_analyst": frozenset({Scope.POLICY_READ, Scope.AUDIT_READ, Scope.REVIEW_DECIDE}),
    "policy_author": frozenset({Scope.POLICY_READ, Scope.POLICY_WRITE}),
    "developer": frozenset({Scope.AI_INVOKE, Scope.POLICY_READ}),
    "agent": frozenset({Scope.AI_INVOKE}),
}


class Principal(BaseModel):
    subject: uuid.UUID
    tenant_id: uuid.UUID
    caller_type: Literal["user", "service", "agent"]
    role: str
    scopes: frozenset[str]
    token_id: str


def create_access_token(
    *,
    subject: uuid.UUID,
    tenant_id: uuid.UUID,
    role: str,
    caller_type: Literal["user", "service", "agent"] = "user",
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    if role not in ROLE_SCOPES:
        raise ValueError(f"Unknown role: {role}")
    now = datetime.now(UTC)
    claims = {
        "sub": str(subject),
        "tid": str(tenant_id),
        "ctp": caller_type,
        "role": role,
        "scope": " ".join(sorted(ROLE_SCOPES[role])),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(seconds=settings.access_token_ttl_seconds),
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings | None = None) -> Principal:
    settings = settings or get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],  # pinned: rejects alg=none / alg confusion
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "nbf", "sub", "iss", "aud", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid token") from exc

    if claims.get("typ") != "access":
        raise AuthenticationError("Invalid token type")
    role = claims.get("role")
    if role not in ROLE_SCOPES:
        raise AuthenticationError("Invalid role claim")
    # Scopes in the token can never exceed what the role grants.
    granted = set(str(claims.get("scope", "")).split()) & set(ROLE_SCOPES[role])
    try:
        return Principal(
            subject=uuid.UUID(claims["sub"]),
            tenant_id=uuid.UUID(claims["tid"]),
            caller_type=claims["ctp"],
            role=role,
            scopes=frozenset(granted),
            token_id=claims["jti"],
        )
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Malformed token claims") from exc


_bearer = HTTPBearer(auto_error=False)


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Missing bearer token")
    return decode_access_token(credentials.credentials)


def require_scopes(*required: Scope):
    async def _checker(principal: Principal = Depends(get_current_principal)) -> Principal:
        missing = {s.value for s in required} - principal.scopes
        if missing:
            raise AuthorisationError(f"Missing required scope(s): {', '.join(sorted(missing))}")
        return principal

    return _checker
