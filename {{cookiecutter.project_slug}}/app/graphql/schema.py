{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
{%- set realtime = cookiecutter.use_sse == "y" or cookiecutter.use_long_polling == "y" or cookiecutter.use_websocket == "y" -%}
"""GraphQL over the item service: queries, mutations{% if realtime %} and the item event subscription{% endif %}.

Resolvers call ``ItemService`` exactly as the REST endpoints do. Operations are served at
``/graphql`` (HTTP) and, for subscriptions, over the graphql-transport-ws protocol.
"""
{%- if realtime %}
import logging
from collections.abc import AsyncGenerator
{%- endif %}
from datetime import datetime
from typing import Any
from uuid import UUID

import strawberry
from graphql import GraphQLError
from pydantic import ValidationError
from strawberry.extensions import DisableIntrospection, QueryDepthLimiter
from strawberry.fastapi import GraphQLRouter
from strawberry.types import Info
from strawberry.types.execution import ExecutionContext

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.graphql.context import GraphQLContext, get_context
from app.models.item import Item as ItemRow
{%- if realtime %}
from app.realtime.broker import BrokerUnavailable
from app.realtime.follow import follow
{%- endif %}
from app.schemas.v1.item import ItemCreate, ItemUpdate

MAX_QUERY_DEPTH = 10
{%- if realtime %}

logger = logging.getLogger(__name__)
{%- endif %}


@strawberry.type(name="Item")
class ItemType:
    id: UUID
    name: str
    description: str | None
    created_at: datetime


@strawberry.type
class ItemList:
    items: list[ItemType]
    total: int
    limit: int
    offset: int
{%- if realtime %}


@strawberry.type
class ItemEvent:
    """An item changed. Like every event it carries ids only: query ``item(id)`` for more."""

    id: str
    type: str
    item_id: UUID
{%- endif %}


def to_type(row: ItemRow) -> ItemType:
    return ItemType(
        id=row.id, name=row.name, description=row.description, created_at=row.created_at
    )


def invalid(exc: ValidationError) -> ValueError:
    """The first validation problem as a plain error message."""
    first = exc.errors()[0]
    where = ".".join(str(part) for part in first["loc"])
    return ValueError(f"{where}: {first['msg']}")


@strawberry.type
class Query:
    @strawberry.field
    async def items(
        self, info: Info[GraphQLContext, None], limit: int = 20, offset: int = 0
    ) -> ItemList:
{%- if rbac %}
        await info.context.require("items:read")
{%- endif %}
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("offset must not be negative")
        rows, total = await info.context.items.list(limit, offset)
        return ItemList(items=[to_type(r) for r in rows], total=total, limit=limit, offset=offset)

    @strawberry.field
    async def item(self, info: Info[GraphQLContext, None], id: UUID) -> ItemType | None:
        """The item, or null when it does not exist (or is not visible to the caller)."""
{%- if rbac %}
        await info.context.require("items:read")
{%- endif %}
        try:
            return to_type(await info.context.items.get(id))
        except AppError as exc:
            if exc.status_code == 404:
                return None
            raise


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def create_item(
        self, info: Info[GraphQLContext, None], name: str, description: str | None = None
    ) -> ItemType:
{%- if rbac %}
        await info.context.require("items:write")
{%- endif %}
        try:
            body = ItemCreate(name=name, description=description)
        except ValidationError as exc:
            raise invalid(exc) from exc
        return to_type(await info.context.items.create(body.name, body.description))

    @strawberry.mutation
    async def update_item(
        self,
        info: Info[GraphQLContext, None],
        id: UUID,
        name: str | None = strawberry.UNSET,
        description: str | None = strawberry.UNSET,
    ) -> ItemType:
        """Change the given fields only; ``description: null`` clears it."""
{%- if rbac %}
        await info.context.require("items:write")
{%- endif %}
        given = {"name": name, "description": description}
        try:
            body = ItemUpdate(**{k: v for k, v in given.items() if v is not strawberry.UNSET})
        except ValidationError as exc:
            raise invalid(exc) from exc
        row = await info.context.items.update(id, body.model_dump(exclude_unset=True))
        return to_type(row)

    @strawberry.mutation
    async def delete_item(self, info: Info[GraphQLContext, None], id: UUID) -> bool:
{%- if rbac %}
        await info.context.require("items:write")
{%- endif %}
        await info.context.items.delete(id)
        return True
{%- if realtime %}


@strawberry.type
class Subscription:
    @strawberry.subscription
    async def item_events(
        self, info: Info[GraphQLContext, None]
    ) -> AsyncGenerator[ItemEvent, None]:
        """Item changes from the moment of subscribing, for the caller's own scope only."""
        context = info.context
{%- if rbac %}
        await context.require("items:read")
{%- endif %}
        async with context.realtime.slot():
            # Nothing below reads the database: release the connection for the duration.
            await context.session.close()
            cursor = await context.broker.latest_id(context.channel) or context.broker.start_id
            try:
                async for event in follow(
                    context.broker, context.channel, cursor, context.realtime.closing
                ):
                    yield ItemEvent(id=event.id, type=event.type, item_id=UUID(event.data["id"]))
            except BrokerUnavailable as exc:
                # The class only: a message can contain the broker URL and credentials.
                logger.warning("item events ended: broker unavailable (%s)", type(exc).__name__)
                raise AppError(503, "events_unavailable", "Events are unavailable") from exc
{%- endif %}


class AppSchema(strawberry.Schema):
    def process_errors(
        self, errors: list[GraphQLError], execution_context: ExecutionContext | None = None
    ) -> None:
        # Domain errors (not found, forbidden, ...) are answers, not faults: no traceback log.
        unexpected = [e for e in errors if not isinstance(e.original_error, AppError)]
        super().process_errors(unexpected, execution_context)


def create_graphql_router() -> GraphQLRouter[GraphQLContext, None]:
    """The router for ``/graphql``; built per app so each app reads its own settings."""
    settings = get_settings()
    prod = settings.environment == "prod"
    # Classes or factories: Strawberry builds a fresh extension for each operation.
    extensions: list[Any] = [lambda: QueryDepthLimiter(max_depth=MAX_QUERY_DEPTH)]
    if prod:
        extensions.append(DisableIntrospection)
    schema = AppSchema(
        query=Query,
        mutation=Mutation,
{%- if realtime %}
        subscription=Subscription,
{%- endif %}
        extensions=extensions,
    )
    origins = [o for o in settings.cors_origins if o != "*"]
    return GraphQLRouter(
        schema,
        context_getter=get_context,
        graphql_ide=None if prod else "graphiql",
        # Browsers do not apply CORS to WebSockets: a subscription handshake must come from
        # this server's own origin or a configured one, or any page could ride on a visitor's login.
        allowed_origins=origins,
    )
