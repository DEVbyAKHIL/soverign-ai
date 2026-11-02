"""Liveness, readiness and Prometheus metrics."""

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from gateway.config import get_settings
from gateway.db import get_engine
from gateway.logging_config import get_logger
from gateway.redis_client import get_redis

router = APIRouter(tags=["health"])
log = get_logger(__name__)


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "version": get_settings().app_version}


async def check_database() -> bool:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        log.warning("readiness_database_failed")
        return False


async def check_redis() -> bool:
    try:
        return bool(await get_redis().ping())
    except Exception:
        log.warning("readiness_redis_failed")
        return False


@router.get("/readyz")
async def readyz() -> JSONResponse:
    checks = {"database": await check_database(), "redis": await check_redis()}
    ready = all(checks.values())
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "checks": checks},
        status_code=200 if ready else 503,
    )


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
