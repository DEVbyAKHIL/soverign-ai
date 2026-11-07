"""reporting-api: FastAPI service serving the streaming aggregates (uvicorn reporting.main:app).

Security model: every metrics endpoint needs a gateway-issued JWT with the audit:read scope; the
tenant always comes from the token (there is no tenant parameter). The service connects to
PostgreSQL as reporting_ro, which can only SELECT aggregate tables.
"""

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import structlog
from fastapi import Depends, FastAPI, Path, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from gateway.core.errors import (
    AuthenticationError,
    AuthorisationError,
    GatewayError,
    register_error_handlers,
)
from gateway.core.security import Principal, Scope, decode_access_token
from gateway.logging_config import configure_logging, get_logger
from reporting import queries
from reporting.config import ReportingSettings, get_reporting_settings
from reporting.schemas import AgentRisk, AgentRiskList, MetricSeries, Overview

log = get_logger("reporting-api")
REQUESTS = Counter("reporting_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("reporting_http_request_seconds", "HTTP request latency", ["route"])
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
Metric = Annotated[str | None, Query(pattern=r"^[a-z_]{1,40}$", description="filter one metric")]


class InvalidQuery(GatewayError):
    status_code = 422
    code = "invalid_query"


class NotFound(GatewayError):
    status_code = 404
    code = "not_found"


_bearer = HTTPBearer(auto_error=False)


def settings_of(request: Request) -> ReportingSettings:
    return request.app.state.settings


async def principal(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Missing bearer token")
    # Same verification as the gateway (pinned algorithm, issuer, audience, role-bounded scopes).
    p = decode_access_token(credentials.credentials, settings=settings_of(request))  # type: ignore[arg-type]
    if Scope.AUDIT_READ.value not in p.scopes:
        raise AuthorisationError("Missing required scope(s): audit:read")
    return p


async def connection(request: Request) -> AsyncIterator[AsyncConnection]:
    engine: AsyncEngine = request.app.state.engine
    async with engine.connect() as conn:
        yield conn


class Range:
    def __init__(self, start: datetime, end: datetime, limit: int) -> None:
        self.start, self.end, self.limit = start, end, limit


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def time_range(
    request: Request,
    from_: Annotated[
        datetime | None, Query(alias="from", description="ISO-8601; default to-60m")
    ] = None,
    to: Annotated[datetime | None, Query(description="ISO-8601; default now")] = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 500,
) -> Range:
    s = settings_of(request)
    end = _utc(to) if to else datetime.now(UTC)
    start = _utc(from_) if from_ else end - timedelta(minutes=s.default_range_minutes)
    if start >= end:
        raise InvalidQuery("'from' must be before 'to'")
    if end - start > timedelta(days=s.max_range_days):
        raise InvalidQuery(f"time range may not exceed {s.max_range_days} days")
    return Range(start, end, min(limit, s.max_page_size))


Auth = Annotated[Principal, Depends(principal)]
Conn = Annotated[AsyncConnection, Depends(connection)]
Win = Annotated[Range, Depends(time_range)]
Cursor = Annotated[
    str | None, Query(max_length=512, description="next_cursor from a previous page")
]


async def _series(
    request: Request, conn, p: Principal, kind: str, r: Range, cursor, filters
) -> MetricSeries:
    try:
        page = await queries.series(
            conn,
            kind,
            tenant_id=p.tenant_id,
            start=r.start,
            end=r.end,
            limit=r.limit,
            cursor=cursor,
            filters=filters,
            suppress_below=settings_of(request).suppress_below,
        )
    except queries.CursorError as exc:
        raise InvalidQuery("invalid cursor") from exc
    return MetricSeries(
        tenant_id=p.tenant_id,
        range_from=r.start,
        range_to=r.end,
        as_of=page.as_of,
        items=page.items,
        next_cursor=page.next_cursor,
    )


def build_router(app: FastAPI) -> None:
    m = "/v1/metrics"
    resp = {"response_model_by_alias": True}

    @app.get(f"{m}/overview", response_model=Overview, tags=["metrics"])
    async def metrics_overview(p: Auth, conn: Conn) -> Overview:
        data = await queries.overview(conn, p.tenant_id)
        top = await queries.latest_agent_risk(
            conn,
            tenant_id=p.tenant_id,
            start=datetime.now(UTC) - timedelta(days=1),
            end=datetime.now(UTC) + timedelta(minutes=10),
            limit=5,
        )
        return Overview(tenant_id=p.tenant_id, top_risky_agents=top, **data)

    @app.get(f"{m}/requests", response_model=MetricSeries, tags=["metrics"], **resp)
    async def metrics_requests(
        request: Request, p: Auth, conn: Conn, r: Win, cursor: Cursor = None,
        agent_id: uuid.UUID | None = None, metric: Metric = None,
    ) -> MetricSeries:  # fmt: skip
        """1-minute tumbling. Without agent_id: tenant totals; with agent_id: that agent."""
        filters = {"agent_id": agent_id or queries.NIL_AGENT, "metric": metric}
        return await _series(request, conn, p, "requests", r, cursor, filters)

    @app.get(f"{m}/policies", response_model=MetricSeries, tags=["metrics"], **resp)
    async def metrics_policies(
        request: Request, p: Auth, conn: Conn, r: Win, cursor: Cursor = None,
        decision: Annotated[str | None, Query(pattern=r"^(\*|[a-z_]{1,32})$")] = None,
        metric: Metric = None,
    ) -> MetricSeries:  # fmt: skip
        """Decisions by type (1-minute tumbling, metric=count); deny_rate and
        failed_evaluations (5-minute sliding, decision='*')."""
        return await _series(
            request, conn, p, "policies", r, cursor, {"decision": decision, "metric": metric}
        )

    @app.get(f"{m}/latency", response_model=MetricSeries, tags=["metrics"], **resp)
    async def metrics_latency(
        request: Request, p: Auth, conn: Conn, r: Win, cursor: Cursor = None, metric: Metric = None
    ) -> MetricSeries:
        """avg_ms, p99_ms, max_ms over 5-minute sliding windows (1-minute slide)."""
        return await _series(request, conn, p, "latency", r, cursor, {"metric": metric})

    @app.get(f"{m}/pii", response_model=MetricSeries, tags=["metrics"], **resp)
    async def metrics_pii(
        request: Request, p: Auth, conn: Conn, r: Win, cursor: Cursor = None,
        destination_model: Annotated[str | None, Query(max_length=200)] = None,
        metric: Metric = None,
    ) -> MetricSeries:  # fmt: skip
        """PII per destination model, 5-minute tumbling. Small cells are suppressed."""
        filters = {"destination_model": destination_model, "metric": metric}
        return await _series(request, conn, p, "pii", r, cursor, filters)

    @app.get(f"{m}/injections", response_model=MetricSeries, tags=["metrics"], **resp)
    async def metrics_injections(
        request: Request, p: Auth, conn: Conn, r: Win, cursor: Cursor = None,
        agent_id: uuid.UUID | None = None, metric: Metric = None,
    ) -> MetricSeries:  # fmt: skip
        """Injection attempts per agent, 5-minute sliding windows."""
        filters = {"agent_id": agent_id, "metric": metric}
        return await _series(request, conn, p, "injections", r, cursor, filters)

    @app.get(f"{m}/agents", response_model=AgentRiskList, tags=["metrics"], **resp)
    async def metrics_agents(p: Auth, conn: Conn, r: Win) -> AgentRiskList:
        """Each agent's latest 10-minute risk window in range, riskiest first."""
        items = await queries.latest_agent_risk(
            conn, tenant_id=p.tenant_id, start=r.start, end=r.end, limit=r.limit
        )
        return AgentRiskList(tenant_id=p.tenant_id, range_from=r.start, range_to=r.end, items=items)

    @app.get(f"{m}/sovereignty", response_model=MetricSeries, tags=["metrics"], **resp)
    async def metrics_sovereignty(
        request: Request, p: Auth, conn: Conn, r: Win, cursor: Cursor = None,
        destination_jurisdiction: Literal["eu", "non_eu", "unknown"] | None = None,
        sensitivity_level: Literal[
            "public", "internal", "confidential", "restricted", "high_risk"
        ] | None = None,
        metric: Metric = None,
    ) -> MetricSeries:  # fmt: skip
        """Volume by jurisdiction x sensitivity, 10-minute tumbling. Small cells suppressed."""
        filters = {
            "destination_jurisdiction": destination_jurisdiction,
            "sensitivity_level": sensitivity_level,
            "metric": metric,
        }
        return await _series(request, conn, p, "sovereignty", r, cursor, filters)

    @app.get("/v1/agents/{agent_id}/risk", response_model=list[AgentRisk], tags=["metrics"])
    async def agent_risk(
        p: Auth, conn: Conn, r: Win, agent_id: Annotated[uuid.UUID, Path()]
    ) -> list[AgentRisk]:
        """Risk history of one agent. 404 if the agent has no data in the caller's tenant."""
        rows = await queries.agent_history(
            conn, tenant_id=p.tenant_id, agent_id=agent_id, start=r.start, end=r.end
        )
        if rows is None:
            raise NotFound("Agent not found")
        return rows[: r.limit]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("startup", environment=app.state.settings.environment)
    yield
    await app.state.engine.dispose()
    log.info("shutdown")


def create_app(settings: ReportingSettings | None = None) -> FastAPI:
    s = settings or get_reporting_settings()
    configure_logging(s.log_level)
    app = FastAPI(
        title="Sovereign AI Gateway - Reporting API",
        version=s.app_version,
        lifespan=lifespan,
        docs_url="/docs" if s.is_development else None,
        redoc_url=None,
        openapi_url="/openapi.json" if s.is_development else None,
    )
    app.state.settings = s
    app.state.engine = create_async_engine(
        s.reporting_database_url,
        **({"poolclass": NullPool} if s.environment == "test" else {"pool_pre_ping": True}),
    )
    register_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Authorization", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next) -> Response:
        try:
            request_id = str(uuid.UUID(request.headers.get("x-request-id", "")))
        except ValueError:
            request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > s.max_request_bytes):
            return JSONResponse(
                {"error": {"code": "payload_too_large", "message": "Request body too large",
                           "request_id": request_id}},
                status_code=413,
            )  # fmt: skip
        started = time.perf_counter()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        REQUESTS.labels(request.method, route, response.status_code).inc()
        LATENCY.labels(route).observe(time.perf_counter() - started)
        response.headers["X-Request-ID"] = request_id
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if not s.is_development:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict:
        return {"status": "ok", "version": s.app_version}

    @app.get("/readyz", tags=["ops"])
    async def readyz() -> JSONResponse:
        try:
            async with app.state.engine.connect() as conn:
                await conn.execute(text("select 1"))
        except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
            log.warning("not_ready", error=type(exc).__name__)
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return JSONResponse({"status": "ready"})

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    build_router(app)
    return app


def __getattr__(name: str):  # lazy `app` for uvicorn without building it at import time
    if name == "app":
        return create_app()
    raise AttributeError(name)
