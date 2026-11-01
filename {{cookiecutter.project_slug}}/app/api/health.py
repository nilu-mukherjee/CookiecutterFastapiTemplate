{% if cookiecutter.use_redis == "y" %}import logging

{% endif %}from fastapi import APIRouter
from sqlalchemy import text

{% if cookiecutter.use_redis == "y" %}from app.api.deps import RedisDep, SessionDep
{% else %}from app.api.deps import SessionDep
{% endif %}from app.core.exceptions import AppError

router = APIRouter(prefix="/health", tags=["health"])
{%- if cookiecutter.use_redis == "y" %}
logger = logging.getLogger("app.health")
{%- endif %}


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
{%- if cookiecutter.use_redis == "y" %}
async def ready(session: SessionDep, redis: RedisDep) -> dict[str, object]:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise AppError(503, "not_ready", "Database unavailable") from exc
    try:
        await redis.ping()
    except Exception as exc:
        # Redis only backs the rate limiter (fails open), so stay ready but report it.
        logger.warning("redis health check failed: %s", exc)
        return {"status": "degraded", "checks": {"database": "ok", "redis": "failing"}}
    return {"status": "ok"}
{%- else %}
async def ready(session: SessionDep) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise AppError(503, "not_ready", "Database unavailable") from exc
    return {"status": "ok"}
{%- endif %}
