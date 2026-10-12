"""Uniform error format: { timestamp, status, code, message, path }.

Messages never echo request bodies or field values (they may contain personal data).
"""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from http import HTTPStatus

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class ApiError(HTTPException):
    """Application error with a machine-readable code.

    Subclasses HTTPException so FastAPI re-raises it unchanged even when it is raised from
    inside request-body parsing (otherwise that would be turned into a generic 400).
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status, detail=message, headers=headers)
        self.status = status
        self.code = code
        self.message = message


def error_response(
    status: int, code: str, message: str, path: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        headers=headers,
        content={
            "timestamp": datetime.now(UTC).isoformat(),
            "status": status,
            "code": code,
            "message": message,
            "path": path,
        },
    )


def _code_for(status: int) -> str:
    try:
        return HTTPStatus(status).phrase.lower().replace(" ", "_")
    except ValueError:
        return "error"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, request.url.path, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else _code_for(exc.status_code)
        return error_response(
            exc.status_code, _code_for(exc.status_code), message, request.url.path
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # loc + msg only: `input` would echo submitted data back (and into logs).
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}"
            for e in exc.errors()[:10]
        )
        return error_response(422, "validation_error", problems, request.url.path)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error on %s (%s)", request.url.path, type(exc).__name__)
        return error_response(500, "internal_error", "Internal server error", request.url.path)
