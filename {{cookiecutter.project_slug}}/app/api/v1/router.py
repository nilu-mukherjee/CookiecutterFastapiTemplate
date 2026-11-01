{%- set auth = cookiecutter.use_auth == "y" -%}
{#- /auth/login and /auth/logout exist for the JWT methods and for sessions. -#}
{%- set api_key = auth and cookiecutter.use_auth_api_key == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
{%- set tenancy = auth and cookiecutter.use_multitenancy == "y" -%}
{%- set social = auth and cookiecutter.use_social_login == "y" -%}
{%- set use_jwt = auth and (cookiecutter.use_auth_jwt_bearer == "y" or cookiecutter.use_auth_jwt_cookie == "y" or cookiecutter.use_auth_session == "y") -%}
{%- set modules = [] -%}
{%- if api_key %}{% set _ = modules.append("api_keys") %}{% endif -%}
{%- if use_jwt %}{% set _ = modules.append("auth") %}{% endif -%}
{%- if cookiecutter.use_sse == "y" %}{% set _ = modules.append("events") %}{% endif -%}
{%- set _ = modules.append("items") -%}
{%- if social %}{% set _ = modules.append("oauth") %}{% endif -%}
{%- if cookiecutter.use_long_polling == "y" %}{% set _ = modules.append("poll") %}{% endif -%}
{%- if rbac %}{% set _ = modules.append("roles") %}{% endif -%}
{%- if tenancy %}{% set _ = modules.append("tenants") %}{% endif -%}
{%- if auth %}{% set _ = modules.append("users") %}{% endif -%}
from fastapi import APIRouter

from app.api.v1.endpoints import {{ modules | join(", ") }}

router = APIRouter()
{%- if use_jwt %}
router.include_router(auth.router)
{%- endif %}
{%- if social %}
router.include_router(oauth.router)
{%- endif %}
{%- if cookiecutter.use_sse == "y" or cookiecutter.use_long_polling == "y" %}
# Realtime paths have a fixed segment after /items: they go BEFORE /items/{item_id}, which
# would otherwise take that segment for an item id.
{%- if cookiecutter.use_sse == "y" %}
router.include_router(events.router)
{%- endif %}
{%- if cookiecutter.use_long_polling == "y" %}
router.include_router(poll.router)
{%- endif %}
{%- endif %}
router.include_router(items.router)
{%- if auth %}
router.include_router(users.router)
{%- endif %}
{%- if rbac %}
router.include_router(roles.router)
{%- endif %}
{%- if api_key %}
router.include_router(api_keys.router)
{%- endif %}
{%- if tenancy %}
router.include_router(tenants.router)
{%- endif %}
