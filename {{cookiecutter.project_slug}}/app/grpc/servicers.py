{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
{%- set tenancy = auth and cookiecutter.use_multitenancy == "y" -%}
{%- set realtime = cookiecutter.use_sse == "y" or cookiecutter.use_long_polling == "y" or cookiecutter.use_websocket == "y" -%}
"""The item service over gRPC: the same ``ItemService`` the REST and GraphQL APIs call."""
import functools
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

{% if realtime %}import grpc
{% endif %}from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import AppError
from app.grpc.generated import items_pb2, items_pb2_grpc
from app.grpc.interceptors import {% if rbac or tenancy %}current_principal, {% endif %}map_error
from app.models.item import Item
{%- if realtime %}
from app.realtime.broker import BrokerUnavailable, EventBroker, channel_for
from app.realtime.follow import follow
from app.realtime.state import RealtimeState
{%- endif %}
from app.schemas.v1.item import ItemCreate, ItemUpdate
from app.services.item import ItemService
{%- if rbac %}
from app.services.rbac import RbacService
{%- endif %}

DEFAULT_LIMIT = 20


def to_message(row: Item) -> items_pb2.Item:
    message = items_pb2.Item(id=str(row.id), name=row.name, description=row.description)
    message.created_at.FromDatetime(row.created_at)
    return message


def parse_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise AppError(400, "invalid_id", "Invalid item id") from exc


def invalid(exc: ValidationError) -> AppError:
    """The first validation problem as a domain error (INVALID_ARGUMENT)."""
    first = exc.errors()[0]
    where = ".".join(str(part) for part in first["loc"])
    return AppError(422, "validation_error", f"{where}: {first['msg']}")


def translated(
    method: Callable[[Any, Any, Any], Awaitable[Any]],
) -> Callable[[Any, Any, Any], Awaitable[Any]]:
    """Answer a domain error with its gRPC status instead of an INTERNAL failure."""

    @functools.wraps(method)
    async def wrapper(self: Any, request: Any, context: Any) -> Any:
        try:
            return await method(self, request, context)
        except AppError as exc:
            await context.abort(map_error(exc), exc.detail)

    return wrapper


class ItemServicer(items_pb2_grpc.ItemServiceServicer):
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
{%- if realtime %}
        broker: EventBroker,
        realtime: RealtimeState,
{%- endif %}
    ) -> None:
        self._sessionmaker = sessionmaker
{%- if realtime %}
        self._broker = broker
        self._realtime = realtime
{%- endif %}
{%- if rbac %}

    async def _require(self, session: AsyncSession, permission: str) -> None:
        principal = current_principal.get()
        if principal.is_superuser:
            return
        granted = await RbacService(session{% if tenancy %}, principal.tenant_id{% endif %}).permissions_for(
            principal.user_id
        )
        if permission not in granted:
            raise AppError(403, "permission_denied", f"Missing required permission: {permission}")
{%- endif %}

    @asynccontextmanager
    async def _items(self, permission: str) -> AsyncIterator[ItemService]:
        """The item service for one call, after the caller's permission is checked."""
        async with self._sessionmaker() as session:
{%- if rbac %}
            await self._require(session, permission)
{%- endif %}
            yield ItemService(
                session{% if tenancy %}, current_principal.get().tenant_id{% endif %}{% if realtime %}, self._broker{% endif %}
            )

    @translated
    async def GetItem(self, request: items_pb2.GetItemRequest, context: Any) -> items_pb2.Item:
        async with self._items("items:read") as items:
            return to_message(await items.get(parse_id(request.id)))

    @translated
    async def ListItems(
        self, request: items_pb2.ListItemsRequest, context: Any
    ) -> items_pb2.ListItemsResponse:
        limit = request.limit or DEFAULT_LIMIT
        if not 1 <= limit <= 100:
            raise AppError(400, "invalid_limit", "limit must be between 1 and 100")
        if request.offset < 0:
            raise AppError(400, "invalid_offset", "offset must not be negative")
        async with self._items("items:read") as items:
            rows, total = await items.list(limit, request.offset)
            return items_pb2.ListItemsResponse(
                items=[to_message(r) for r in rows], total=total, limit=limit, offset=request.offset
            )

    @translated
    async def CreateItem(
        self, request: items_pb2.CreateItemRequest, context: Any
    ) -> items_pb2.Item:
        description = request.description if request.HasField("description") else None
        try:
            body = ItemCreate(name=request.name, description=description)
        except ValidationError as exc:
            raise invalid(exc) from exc
        async with self._items("items:write") as items:
            return to_message(await items.create(body.name, body.description))

    @translated
    async def UpdateItem(
        self, request: items_pb2.UpdateItemRequest, context: Any
    ) -> items_pb2.Item:
        given: dict[str, str | None] = {}
        if request.HasField("name"):
            given["name"] = request.name
        if request.HasField("description"):
            given["description"] = request.description or None  # empty clears it
        try:
            body = ItemUpdate(**given)
        except ValidationError as exc:
            raise invalid(exc) from exc
        item_id = parse_id(request.id)
        async with self._items("items:write") as items:
            return to_message(await items.update(item_id, body.model_dump(exclude_unset=True)))

    @translated
    async def DeleteItem(
        self, request: items_pb2.DeleteItemRequest, context: Any
    ) -> items_pb2.DeleteItemResponse:
        item_id = parse_id(request.id)
        async with self._items("items:write") as items:
            await items.delete(item_id)
        return items_pb2.DeleteItemResponse()
{%- if realtime %}

    async def StreamItemEvents(
        self, request: items_pb2.StreamItemEventsRequest, context: Any
    ) -> AsyncIterator[items_pb2.ItemEvent]:
        try:
            async with self._items("items:read"):
                pass  # authorizes, then releases the database connection for the stream
            principal_channel = channel_for({% if tenancy %}current_principal.get().tenant_id{% else %}None{% endif %})
            async with self._realtime.slot():
                cursor = await self._broker.latest_id(principal_channel) or self._broker.start_id
                async for event in follow(
                    self._broker, principal_channel, cursor, self._realtime.closing
                ):
                    yield items_pb2.ItemEvent(
                        id=event.id, type=event.type, item_id=event.data["id"]
                    )
        except AppError as exc:
            await context.abort(map_error(exc), exc.detail)
        except BrokerUnavailable:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "Events are unavailable")
{%- endif %}
