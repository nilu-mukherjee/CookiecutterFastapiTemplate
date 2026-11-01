import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.middleware.request_id import request_id_var

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        detail: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.headers = headers


def _body(request: Request, code: str, detail: str) -> dict[str, str | None]:
    # Set by RequestIdMiddleware in scope state (shared with mounted sub-apps).
    request_id = getattr(request.state, "request_id", None) or request_id_var.get()
    return {"code": code, "detail": detail, "request_id": request_id}


async def _app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return JSONResponse(
        _body(request, exc.code, exc.detail),
        status_code=exc.status_code,
        headers=exc.headers,
    )


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return JSONResponse(
        _body(request, "http_error", str(exc.detail)),
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    detail = "; ".join(
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
    )
    return JSONResponse(_body(request, "validation_error", detail), status_code=422)


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error", exc_info=exc)
    return JSONResponse(
        _body(request, "internal_error", "Internal server error"), status_code=500
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled)
