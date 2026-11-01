{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set redis = cookiecutter.use_redis == "y" -%}
{%- set lifespan = auth or redis -%}
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
from app.services.user import UserService
{%- endif %}
{%- if lifespan %}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
{%- if auth %}
    async with get_sessionmaker()() as session:
        await UserService(session).bootstrap_superuser()
{%- endif %}
    yield
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
