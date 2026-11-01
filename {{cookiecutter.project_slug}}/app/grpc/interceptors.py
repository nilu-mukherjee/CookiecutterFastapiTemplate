{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
"""gRPC status codes for domain errors{% if auth %}, and authentication of calls{% endif %}."""
{% if auth %}from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextvars import ContextVar, Token
from typing import Any

{% endif %}import grpc
{% if auth %}from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import HTTPConnection

from app.core.auth import Principal, authenticate_connection
{% else %}
{% endif %}from app.core.exceptions import AppError

_STATUS = {
    400: grpc.StatusCode.INVALID_ARGUMENT,
    401: grpc.StatusCode.UNAUTHENTICATED,
{%- if rbac %}
    403: grpc.StatusCode.PERMISSION_DENIED,
{%- endif %}
    404: grpc.StatusCode.NOT_FOUND,
    409: grpc.StatusCode.ALREADY_EXISTS,
    422: grpc.StatusCode.INVALID_ARGUMENT,
    429: grpc.StatusCode.RESOURCE_EXHAUSTED,
    503: grpc.StatusCode.UNAVAILABLE,
}


def map_error(error: AppError) -> grpc.StatusCode:
    """The gRPC status for a domain error (anything unlisted is INTERNAL)."""
    return _STATUS.get(error.status_code, grpc.StatusCode.INTERNAL)
{%- if auth %}

# The method path of every call to the item service; other services (reflection) are not
# authenticated.
ITEM_SERVICE_PREFIX = "/items.v1.ItemService/"

current_principal: ContextVar[Principal] = ContextVar("current_principal")


def _connection(metadata: Iterable[tuple[str, str | bytes]]) -> HTTPConnection:
    """The call's credentials as the request the REST authenticators expect.

    Only ``authorization: Bearer ...`` and ``x-api-key`` are passed on, so a gRPC call can
    never authenticate with a cookie, a session or HTTP Basic.
    """
    headers: list[tuple[bytes, bytes]] = []
    for key, value in metadata:
        if not isinstance(value, str):
            continue  # binary (-bin) metadata
        if key == "authorization" and value.lower().startswith("bearer "):
            headers.append((b"authorization", value.encode()))
        elif key == "x-api-key":
            headers.append((b"x-api-key", value.encode()))
    return HTTPConnection({"type": "http", "headers": headers, "scheme": "https"})


class AuthInterceptor(grpc.aio.ServerInterceptor):
    """Authenticates item calls with the REST authenticators and exposes the principal.

    The caller's ``Principal`` is available to the servicer as ``current_principal``.
    A call without a valid credential never reaches the servicer.
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def intercept_service(
        self,
        continuation: Callable[[Any], Awaitable[Any]],
        handler_call_details: Any,
    ) -> Any:
        handler = await continuation(handler_call_details)
        if handler is None or not handler_call_details.method.startswith(ITEM_SERVICE_PREFIX):
            return handler
        if handler.unary_unary:
            return grpc.unary_unary_rpc_method_handler(
                self._unary(handler.unary_unary),
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )
        if handler.unary_stream:
            return grpc.unary_stream_rpc_method_handler(
                self._stream(handler.unary_stream),
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )
        return handler

    async def _authenticate(self, context: Any) -> Token[Principal]:
        try:
            async with self._sessionmaker() as session:
                principal = await authenticate_connection(
                    _connection(context.invocation_metadata()), session
                )
            if principal is None:
                raise AppError(401, "not_authenticated", "Not authenticated")
            return current_principal.set(principal)
        except AppError as exc:
            await context.abort(map_error(exc), exc.detail)  # raises: the call ends here
            raise

    def _unary(self, behavior: Callable[[Any, Any], Awaitable[Any]]) -> Any:
        async def authenticated(request: Any, context: Any) -> Any:
            token = await self._authenticate(context)
            try:
                return await behavior(request, context)
            finally:
                current_principal.reset(token)

        return authenticated

    def _stream(self, behavior: Callable[[Any, Any], AsyncIterator[Any]]) -> Any:
        async def authenticated(request: Any, context: Any) -> AsyncIterator[Any]:
            token = await self._authenticate(context)
            try:
                async for message in behavior(request, context):
                    yield message
            finally:
                current_principal.reset(token)

        return authenticated
{%- endif %}
