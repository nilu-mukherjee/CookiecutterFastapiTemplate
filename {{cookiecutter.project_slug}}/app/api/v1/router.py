{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set bearer = auth and cookiecutter.use_auth_jwt_bearer == "y" -%}
from fastapi import APIRouter

{% if bearer %}from app.api.v1.endpoints import auth, items, users
{% elif auth %}from app.api.v1.endpoints import items, users
{% else %}from app.api.v1.endpoints import items
{% endif %}
router = APIRouter()
{%- if bearer %}
router.include_router(auth.router)
{%- endif %}
router.include_router(items.router)
{%- if auth %}
router.include_router(users.router)
{%- endif %}
