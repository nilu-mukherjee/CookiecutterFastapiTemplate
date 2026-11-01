from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import SessionDep
from app.core.exceptions import AppError

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(session: SessionDep) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise AppError(503, "not_ready", "Database unavailable") from exc
    return {"status": "ok"}
