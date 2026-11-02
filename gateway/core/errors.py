"""Structured error types and handlers. Internal details never reach the client."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from gateway.logging_config import get_logger

log = get_logger(__name__)


class GatewayError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str = "Internal server error") -> None:
        super().__init__(message)
        self.message = message


class AuthenticationError(GatewayError):
    status_code = 401
    code = "unauthenticated"


class AuthorisationError(GatewayError):
    status_code = 403
    code = "forbidden"


class TenantIsolationError(GatewayError):
    status_code = 404  # do not reveal that another tenant's resource exists
    code = "not_found"


class RateLimitExceeded(GatewayError):
    status_code = 429
    code = "rate_limited"


class PayloadTooLarge(GatewayError):
    status_code = 413
    code = "payload_too_large"


def _body(request: Request, code: str, message: str) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(request.state, "request_id", None),
        }
    }


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(GatewayError)
    async def _gateway_error(request: Request, exc: GatewayError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        log.info("request_rejected", code=exc.code, status=exc.status_code, path=request.url.path)
        return JSONResponse(
            _body(request, exc.code, exc.message), status_code=exc.status_code, headers=headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Field locations only; never echo submitted values (they may contain PII).
        fields = [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
        return JSONResponse(
            _body(request, "validation_error", f"Invalid fields: {', '.join(fields)}"),
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path)
        return JSONResponse(
            _body(request, "internal_error", "Internal server error"), status_code=500
        )
