"""Import every model so Base.metadata is complete (Alembic, tests)."""
{% if cookiecutter.use_auth == "y" and cookiecutter.use_auth_api_key == "y" %}from app.models.api_key import ApiKey
{% endif %}from app.models.base import Base
from app.models.item import Item
{%- if cookiecutter.use_auth == "y" and cookiecutter.use_rbac == "y" %}
from app.models.permission import Permission
{%- endif %}
{%- if cookiecutter.use_auth == "y" and (cookiecutter.use_auth_jwt_bearer == "y" or cookiecutter.use_auth_jwt_cookie == "y") %}
from app.models.refresh_token import RefreshToken
{%- endif %}
{%- if cookiecutter.use_auth == "y" and cookiecutter.use_rbac == "y" %}
from app.models.role import Role
{%- endif %}
{%- if cookiecutter.use_auth == "y" and cookiecutter.use_auth_session == "y" %}
from app.models.session import Session
{%- endif %}
{%- if cookiecutter.use_auth == "y" %}
from app.models.user import User
{%- endif %}

__all__ = [
{%- if cookiecutter.use_auth == "y" and cookiecutter.use_auth_api_key == "y" %}
    "ApiKey",
{%- endif %}
    "Base",
    "Item",
{%- if cookiecutter.use_auth == "y" and cookiecutter.use_rbac == "y" %}
    "Permission",
{%- endif %}
{%- if cookiecutter.use_auth == "y" and (cookiecutter.use_auth_jwt_bearer == "y" or cookiecutter.use_auth_jwt_cookie == "y") %}
    "RefreshToken",
{%- endif %}
{%- if cookiecutter.use_auth == "y" and cookiecutter.use_rbac == "y" %}
    "Role",
{%- endif %}
{%- if cookiecutter.use_auth == "y" and cookiecutter.use_auth_session == "y" %}
    "Session",
{%- endif %}
{%- if cookiecutter.use_auth == "y" %}
    "User",
{%- endif %}
]
