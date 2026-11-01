{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set bearer = auth and cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
{%- set tenancy = auth and cookiecutter.use_multitenancy == "y" -%}
{%- set realtime = cookiecutter.use_sse == "y" or cookiecutter.use_long_polling == "y" or cookiecutter.use_websocket == "y" -%}
"""What every GraphQL operation can reach, built per HTTP request or per WebSocket connection.

The caller is authenticated by the same authenticators as REST, so credentials,
tenancy and permissions behave the same way.
"""
{%- if auth %}
from fastapi import WebSocketException
{%- endif %}
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection
from strawberry.fastapi import BaseContext

from app.api.deps import {% if realtime %}BrokerDep, RealtimeDep, {% endif %}SessionDep
{%- if auth %}
from app.core.auth import Principal, authenticate_connection
from app.core.exceptions import AppError
{%- endif %}
{%- if realtime %}
from app.realtime.broker import EventBroker, channel_for
from app.realtime.state import RealtimeState
{%- endif %}
from app.services.item import ItemService
{%- if rbac %}
from app.services.rbac import RbacService
{%- endif %}

POLICY_VIOLATION = 1008


class GraphQLContext(BaseContext):
    def __init__(
        self,
        session: AsyncSession,
        items: ItemService,
{%- if auth %}
        principal: Principal,
{%- endif %}
{%- if rbac %}
        rbac: RbacService,
{%- endif %}
{%- if realtime %}
        broker: EventBroker,
        realtime: RealtimeState,
{%- endif %}
    ) -> None:
        super().__init__()
        self.session = session
        self.items = items
{%- if auth %}
        self.principal = principal
{%- endif %}
{%- if rbac %}
        self.rbac = rbac
        self._granted: set[str] | None = None
{%- endif %}
{%- if realtime %}
        self.broker = broker
        self.realtime = realtime
        self.channel = channel_for({% if tenancy %}principal.tenant_id{% else %}None{% endif %})
{%- endif %}
{%- if rbac %}

    async def require(self, permission: str) -> None:
        """Raise a 403 AppError unless the caller holds the permission (superusers do)."""
        if self.principal.is_superuser:
            return
        if self._granted is None:
            self._granted = await self.rbac.permissions_for(self.principal.user_id)
        if permission not in self._granted:
            raise AppError(403, "permission_denied", f"Missing required permission: {permission}")
{%- endif %}


async def get_context(
    connection: HTTPConnection,
    session: SessionDep,
{%- if realtime %}
    broker: BrokerDep,
    realtime: RealtimeDep,
{%- endif %}
) -> GraphQLContext:
{%- if auth %}
    try:
        principal = await authenticate_connection(connection, session)
        if principal is None:
            raise AppError(
                401,
                "not_authenticated",
                "Not authenticated",
{%- if bearer %}
                headers={"WWW-Authenticate": "Bearer"},
{%- endif %}
            )
    except AppError as exc:
        if connection.scope["type"] == "websocket":
            # A subscription handshake: refused with a close code, not an HTTP body.
            raise WebSocketException(code=POLICY_VIOLATION, reason=exc.code) from exc
        raise
{%- endif %}
    return GraphQLContext(
        session,
        ItemService(session{% if tenancy %}, principal.tenant_id{% endif %}{% if realtime %}, broker{% endif %}),
{%- if auth %}
        principal,
{%- endif %}
{%- if rbac %}
        RbacService(session{% if tenancy %}, principal.tenant_id{% endif %}),
{%- endif %}
{%- if realtime %}
        broker,
        realtime,
{%- endif %}
    )
