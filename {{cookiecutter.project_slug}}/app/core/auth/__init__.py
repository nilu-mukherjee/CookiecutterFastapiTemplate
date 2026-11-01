{%- set bearer = cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set jwt_cookie = cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set session = cookiecutter.use_auth_session == "y" -%}
"""Assembles the enabled authentication methods, in precedence order."""
from app.core.auth.base import Authenticator, Principal
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
# Order: Bearer, API key, session cookie, JWT cookie, Basic.
AUTHENTICATORS: list[Authenticator] = [
{%- if bearer %}
    JWTBearerAuthenticator(),
{%- endif %}
{%- if session %}
    SessionAuthenticator(),
{%- endif %}
{%- if jwt_cookie %}
    JWTCookieAuthenticator(),
{%- endif %}
]

__all__ = ["AUTHENTICATORS", "Authenticator", "Principal"]
