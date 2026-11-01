from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Sunset date announced for API v1 (HTTP-date, RFC 9110). Change it here only.
V1_SUNSET = "Fri, 31 Dec 2027 23:59:59 GMT"
V1_PREFIX = "/api/v1/"


class DeprecationMiddleware:
    """Mark /api/v1/ responses as deprecated with a Sunset date."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(V1_PREFIX):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Deprecation"] = "true"
                headers["Sunset"] = V1_SUNSET
            await send(message)

        await self.app(scope, receive, send_wrapper)
