import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.middleware.request_id import request_id_var

logger = logging.getLogger("app.access")


class AccessLogMiddleware:
    """One INFO record per HTTP request, also when the handler raises (status 500)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 3),
                    "request_id": request_id_var.get(),
                },
            )
