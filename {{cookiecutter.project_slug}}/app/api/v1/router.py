{%- set auth = cookiecutter.use_auth == "y" -%}
{#- /auth/login and /auth/logout exist for the JWT methods and for sessions. -#}
{%- set api_key = auth and cookiecutter.use_auth_api_key == "y" -%}
{%- set use_jwt = auth and (cookiecutter.use_auth_jwt_bearer == "y" or cookiecutter.use_auth_jwt_cookie == "y" or cookiecutter.use_auth_session == "y") -%}
from fastapi import APIRouter

{% if use_jwt and api_key %}from app.api.v1.endpoints import api_keys, auth, items, users
{% elif use_jwt %}from app.api.v1.endpoints import auth, items, users
{% elif api_key %}from app.api.v1.endpoints import api_keys, items, users
{% elif auth %}from app.api.v1.endpoints import items, users
{% else %}from app.api.v1.endpoints import items
{% endif %}
router = APIRouter()
{%- if use_jwt %}
router.include_router(auth.router)
{%- endif %}
router.include_router(items.router)
{%- if auth %}
router.include_router(users.router)
{%- endif %}
{%- if api_key %}
router.include_router(api_keys.router)
{%- endif %}
