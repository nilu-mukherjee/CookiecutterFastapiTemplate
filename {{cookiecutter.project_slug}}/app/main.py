{% if cookiecutter.use_redis == "y" %}from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

{% endif %}from fastapi import FastAPI

from app.api.router import mount_routes
{% if cookiecutter.use_redis == "y" %}from app.cache.redis import close_redis
{% endif %}from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import register_middleware
{%- if cookiecutter.use_redis == "y" %}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await close_redis()
{%- endif %}


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="{{ cookiecutter.project_name }}",
        debug=settings.debug,
{%- if cookiecutter.use_redis == "y" %}
        lifespan=lifespan,
{%- endif %}
    )
    register_exception_handlers(app)
    register_middleware(app)
    mount_routes(app)
    return app


app = create_app()
