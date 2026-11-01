{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
{%- set tenancy = auth and cookiecutter.use_multitenancy == "y" -%}
{%- set redis = cookiecutter.use_redis == "y" -%}
{%- set realtime = cookiecutter.use_sse == "y" or cookiecutter.use_long_polling == "y" or cookiecutter.use_websocket == "y" -%}
{%- set lifespan = auth or redis or realtime -%}
{% if lifespan %}from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

{% endif %}from fastapi import FastAPI

from app.api.router import mount_routes
{% if redis %}from app.cache.redis import close_redis
{% endif %}from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import register_middleware
{%- if auth %}
from app.db.session import get_sessionmaker
{%- endif %}
{%- if realtime %}
from app.realtime import RealtimeState, create_broker
{%- endif %}
{%- if auth %}
{%- if rbac %}
from app.services.rbac import RbacService
{%- endif %}
{%- if tenancy %}
from app.services.tenant import TenantService
{%- endif %}
from app.services.user import UserService
{%- endif %}
{%- if lifespan %}


@asynccontextmanager
async def lifespan({% if realtime %}application{% else %}_{% endif %}: FastAPI) -> AsyncIterator[None]:
{%- if auth %}
    async with get_sessionmaker()() as session:
{%- if tenancy %}
        # The platform operator's tenant; the first superuser is created inside it.
        system = await TenantService(session).ensure_system_tenant()
{%- if rbac %}
        await RbacService(session, system.id).ensure_default_roles()
{%- endif %}
        await UserService(session, system.id).bootstrap_superuser()
{%- else %}
{%- if rbac %}
        await RbacService(session).ensure_default_roles()
{%- endif %}
        await UserService(session).bootstrap_superuser()
{%- endif %}
{%- endif %}
{%- if realtime %}
    # One broker per process, chosen here: Redis Streams with Redis, else in-memory.
    application.state.broker = create_broker()
    application.state.realtime = RealtimeState(get_settings().realtime_max_connections)
{%- endif %}
    yield
{%- if realtime %}
    # Tell open streams and polls to end, so shutdown does not wait for their clients.
    application.state.realtime.closing.set()
{%- endif %}
{%- if redis %}
    await close_redis()
{%- endif %}
{%- endif %}


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="{{ cookiecutter.project_name }}",
        debug=settings.debug,
{%- if lifespan %}
        lifespan=lifespan,
{%- endif %}
    )
    register_exception_handlers(app)
    register_middleware(app)
    mount_routes(app)
    return app


app = create_app()
