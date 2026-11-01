{%- set bearer = cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set jwt_cookie = cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set session = cookiecutter.use_auth_session == "y" -%}
{%- set api_key = cookiecutter.use_auth_api_key == "y" -%}
{%- set basic = cookiecutter.use_auth_basic == "y" -%}
"""Assembles the enabled authentication methods, in precedence order."""
{% if api_key %}from app.core.auth.api_key import ApiKeyAuthenticator
{% endif %}from app.core.auth.base import Authenticator, Principal
{%- if basic %}
from app.core.auth.basic import BasicAuthenticator
{%- endif %}
{%- if bearer %}
from app.core.auth.jwt_bearer import JWTBearerAuthenticator
{%- endif %}
{%- if jwt_cookie %}
from app.core.auth.jwt_cookie import JWTCookieAuthenticator
{%- endif %}
{%- if session %}
from app.core.auth.session import SessionAuthenticator
{%- endif %}

# The first authenticator that finds its credential decides (no fall-through).
# Precedence is fixed; only the enabled methods are listed.
AUTHENTICATORS: list[Authenticator] = [
{%- if bearer %}
    JWTBearerAuthenticator(),
{%- endif %}
{%- if api_key %}
    ApiKeyAuthenticator(),
{%- endif %}
{%- if session %}
    SessionAuthenticator(),
{%- endif %}
{%- if jwt_cookie %}
    JWTCookieAuthenticator(),
{%- endif %}
{%- if basic %}
    BasicAuthenticator(),
{%- endif %}
]

__all__ = ["AUTHENTICATORS", "Authenticator", "Principal"]
