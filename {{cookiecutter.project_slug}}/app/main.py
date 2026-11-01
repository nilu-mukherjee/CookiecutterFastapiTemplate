from fastapi import FastAPI

from app.api.router import mount_routes
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import register_middleware


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="{{ cookiecutter.project_name }}", debug=settings.debug)
    register_exception_handlers(app)
    register_middleware(app)
    mount_routes(app)
    return app


app = create_app()
