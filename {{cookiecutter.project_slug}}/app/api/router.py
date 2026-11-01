{%- set realtime = cookiecutter.use_sse == "y" or cookiecutter.use_long_polling == "y" or cookiecutter.use_websocket == "y" -%}
from fastapi import APIRouter, FastAPI

from app.api import health
from app.api.v1.router import router as v1_router
from app.api.v2.router import router as v2_router
from app.core.exceptions import register_exception_handlers
{%- if cookiecutter.use_graphql == "y" %}
from app.graphql.schema import create_graphql_router
{%- endif %}


def _version_app(root: FastAPI, version: str, router: APIRouter) -> FastAPI:
    sub = FastAPI(title=f"{root.title} v{version}", version=version, docs_url="/docs")
    sub.include_router(router)
    register_exception_handlers(sub)
    # Mounted sub-apps do not inherit overrides; share the root's dict.
    sub.dependency_overrides = root.dependency_overrides
{%- if realtime %}
    # Nor its state: share it, so request.app.state.broker is the one the lifespan set.
    sub.state = root.state
{%- endif %}
    return sub


def mount_routes(app: FastAPI) -> None:
    app.include_router(health.router)
    app.mount("/api/v1", _version_app(app, "1", v1_router))
    app.mount("/api/v2", _version_app(app, "2", v2_router))
{%- if cookiecutter.use_graphql == "y" %}
    # Unversioned, as is conventional for GraphQL: the schema evolves without versions.
    app.include_router(create_graphql_router(), prefix="/graphql", include_in_schema=False)
{%- endif %}
