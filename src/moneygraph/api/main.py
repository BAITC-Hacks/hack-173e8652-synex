from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from moneygraph.api.dependencies import build_services
from moneygraph.api.errors import (
    APIError,
    api_error_handler,
    request_id,
    validation_error_handler,
)
from moneygraph.api.routes import agentic, analytics, assistant, health, investigations, runs
from moneygraph.api.settings import APISettings
from moneygraph.services.artifacts import ArtifactUnavailableError

LOGGER = logging.getLogger("moneygraph.api")
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def create_app(settings: APISettings | None = None) -> FastAPI:
    resolved = settings or APISettings()
    services = build_services(resolved)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        services.database.dispose()

    application = FastAPI(
        title=resolved.api_title,
        version=resolved.api_version,
        description=(
            "Bounded, explainable graph exploration and investigation workspace. "
            "All gid/src/dst values are opaque strings."
        ),
        lifespan=lifespan,
    )
    application.state.services = services
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID", "Idempotency-Key"],
        expose_headers=["X-Request-ID"],
    )

    @application.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied = request.headers.get("X-Request-ID", "")
        correlation_id = supplied if SAFE_REQUEST_ID.fullmatch(supplied) else str(uuid4())
        request.state.request_id = correlation_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = correlation_id
        LOGGER.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": correlation_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
                ensure_ascii=False,
            )
        )
        return response

    application.add_exception_handler(APIError, cast(Any, api_error_handler))
    application.add_exception_handler(RequestValidationError, cast(Any, validation_error_handler))
    application.add_exception_handler(ValidationError, cast(Any, validation_error_handler))

    @application.exception_handler(ArtifactUnavailableError)
    async def artifact_error(request: Request, exc: ArtifactUnavailableError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "artifacts_unavailable",
                    "message": str(exc),
                },
                "request_id": request_id(request),
            },
        )

    application.include_router(health.router)
    application.include_router(analytics.router)
    application.include_router(runs.router)
    application.include_router(investigations.router)
    application.include_router(assistant.router)
    application.include_router(agentic.router)
    return application


app = create_app()
