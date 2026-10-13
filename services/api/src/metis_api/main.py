"""FastAPI application factory.

The same app object serves uvicorn locally and Lambda in the cloud (through the
pinned ASGI adapter in ``lambda_entry.py``), so there is no second, partially
implemented HTTP path to keep in sync.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from metis_contracts import CONTRACT_VERSION, ErrorBody, ErrorCode, MetisError, settings

from .deps import build_adapters
from .guards import BodyLimitMiddleware, HostGuardMiddleware, clean_correlation_id
from .routes import captures, notifications, plans, projects, reminders, tasks

log = logging.getLogger("metis.api")

#: Never logged: request bodies, transcripts, prompts, tokens, object URLs.
SAFE_LOG_FIELDS = ("method", "path", "status", "duration_ms", "correlation_id", "user_scope")


def configure_logging() -> None:
    logging.basicConfig(
        level=settings().log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    cfg = settings()
    app.state.adapters = build_adapters()
    log.info(
        "metis api ready mode=%s env=%s contract=v%s adapters=%s",
        cfg.mode.value,
        cfg.environment,
        CONTRACT_VERSION,
        app.state.adapters.describe(),
    )
    if cfg.environment != "prod" and cfg.allow_fixture_identity:
        log.warning("fixture identity is ENABLED; this configuration is for development only")
    yield


def create_app() -> FastAPI:
    cfg = settings()
    app = FastAPI(
        title="Metis API",
        version=f"1.0.0+contract{CONTRACT_VERSION}",
        description=(
            "Voice-first multimodal assistant. Every mutation carries an expected "
            "revision; operations that create work carry an Idempotency-Key."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Correlation-Id"],
        expose_headers=["X-Correlation-Id"],
    )

    # Outermost last: Host is checked first, then the body is bounded as it streams.
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(HostGuardMiddleware)

    @app.middleware("http")
    async def correlate(request: Request, call_next: Callable[[Request], Awaitable[Any]]) -> Any:
        """Attach a correlation id to every request, response and log line.

        This is what makes a missed reminder debuggable: the same id appears on
        the API call, the queue message, the model call and the notification.
        """
        correlation_id = clean_correlation_id(request.headers.get("X-Correlation-Id"))
        request.state.correlation_id = correlation_id
        started = time.monotonic()
        try:
            response = await call_next(request)
        except MetisError as exc:
            response = _error_response(exc, correlation_id)
        duration_ms = int((time.monotonic() - started) * 1000)
        response.headers["X-Correlation-Id"] = correlation_id
        log.info(
            "%s %s -> %s in %dms correlation=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            correlation_id,
        )
        return response

    @app.exception_handler(MetisError)
    async def metis_error_handler(request: Request, exc: MetisError) -> JSONResponse:
        return _error_response(exc, getattr(request.state, "correlation_id", None))

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # The contract's own error shape, so the client has one parser.
        details = {
            ".".join(str(p) for p in error.get("loc", [])): str(error.get("msg", ""))
            for error in exc.errors()[:10]
        }
        return _error_response(
            MetisError(
                ErrorCode.VALIDATION_FAILED, "the request did not match the contract", details=details
            ),
            getattr(request.state, "correlation_id", None),
        )

    @app.exception_handler(Exception)
    async def unexpected_handler(request: Request, exc: Exception) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", None)
        # The message is deliberately generic: internal details stay in logs.
        log.exception("unhandled error correlation=%s", correlation_id)
        return _error_response(MetisError(ErrorCode.INTERNAL, "an unexpected error occurred"), correlation_id)

    for module in (captures, tasks, reminders, plans, projects, notifications):
        app.include_router(module.router)

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {"service": "metis-api", "contract_version": str(CONTRACT_VERSION)}

    return app


def _error_response(exc: MetisError, correlation_id: str | None) -> JSONResponse:
    body: ErrorBody = exc.body(correlation_id)
    headers = {}
    if exc.retry_after_seconds:
        headers["Retry-After"] = str(exc.retry_after_seconds)
    if correlation_id:
        headers["X-Correlation-Id"] = correlation_id
    return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"), headers=headers)


app = create_app()
