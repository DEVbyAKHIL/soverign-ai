"""FastAPI application entrypoint."""

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram

from agents.anomaly_detector import AnomalyDetector
from detection.injection.detector import get_injection_detector
from detection.pipeline import get_pipeline
from gateway.config import get_settings
from gateway.core.errors import register_error_handlers
from gateway.db import dispose_engine
from gateway.logging_config import configure_logging, get_logger
from gateway.redis_client import close_redis
from gateway.routers import agents, audit, auth, decisions, health, infer, policies
from policy.loader import load_policy_dir

REQUESTS = Counter("gateway_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("gateway_http_request_seconds", "HTTP request latency", ["method", "route"])

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

log = get_logger("gateway")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    log.info("startup", environment=settings.environment, version=settings.app_version)
    yield
    log.info("shutdown")
    await close_redis()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="Sovereign AI Access Gateway",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if settings.is_development else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.is_development else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next) -> Response:
        incoming = request.headers.get("x-request-id", "")
        try:
            request_id = str(uuid.UUID(incoming))
        except ValueError:
            request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        declared = request.headers.get("content-length")
        if declared is not None and (
            not declared.isdigit() or int(declared) > settings.max_request_bytes
        ):
            return JSONResponse(
                {
                    "error": {
                        "code": "payload_too_large",
                        "message": "Request body too large",
                        "request_id": request_id,
                    }
                },
                status_code=413,
            )

        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start

        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        REQUESTS.labels(request.method, route_path, response.status_code).inc()
        LATENCY.labels(request.method, route_path).observe(elapsed)

        response.headers["X-Request-ID"] = request_id
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if not settings.is_development:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        log.info(
            "request",
            method=request.method,
            path=route_path,
            status=response.status_code,
            duration_ms=round(elapsed * 1000, 2),
        )
        return response

    register_error_handlers(app)
    # Fail closed: an invalid or unsafe policy set prevents the gateway from starting.
    app.state.policy_set = load_policy_dir(settings.policy_dir)
    log.info(
        "policies_loaded",
        policy_set_version=app.state.policy_set.version,
        policies=[p.ref for p in app.state.policy_set.policies],
    )
    # Fail closed: with NER_MODE=required a missing NER model prevents start-up.
    app.state.detector = get_pipeline(settings.ner_mode)
    app.state.injection = get_injection_detector(
        settings.llm_reviewer_url, settings.llm_reviewer_model, settings.llm_reviewer_timeout_s
    )
    app.state.anomaly = AnomalyDetector()
    log.info(
        "detector_loaded",
        **app.state.detector.model_versions,
        **app.state.injection.model_versions,
        anomaly_detector=app.state.anomaly.version,
    )
    for router in (health, auth, infer, decisions, audit, policies, agents):
        app.include_router(router.router)
    return app


app = create_app()
