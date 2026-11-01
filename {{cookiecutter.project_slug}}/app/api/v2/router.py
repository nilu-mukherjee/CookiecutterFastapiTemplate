from fastapi import APIRouter

from app.api.v2.endpoints import items

router = APIRouter()
{%- if cookiecutter.use_sse == "y" or cookiecutter.use_long_polling == "y" %}
# Realtime item events live in v1 only.
{%- endif %}
router.include_router(items.router)
