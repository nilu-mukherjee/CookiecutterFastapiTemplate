from fastapi import APIRouter

from app.api.v2.endpoints import items

router = APIRouter()
router.include_router(items.router)
