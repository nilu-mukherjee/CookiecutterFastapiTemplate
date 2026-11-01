{%- set bearer = cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set jwt_cookie = cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set session = cookiecutter.use_auth_session == "y" -%}
{%- set api_key = cookiecutter.use_auth_api_key == "y" -%}
{%- set basic = cookiecutter.use_auth_basic == "y" -%}
"""Assembles the enabled authentication methods, in precedence order."""
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

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
# Order: {% set names = [] %}{% if bearer %}{% set _ = names.append("Bearer") %}{% endif %}{% if api_key %}{% set _ = names.append("API key") %}{% endif %}{% if session %}{% set _ = names.append("session cookie") %}{% endif %}{% if jwt_cookie %}{% set _ = names.append("JWT cookie") %}{% endif %}{% if basic %}{% set _ = names.append("Basic") %}{% endif %}{{ names | join(", ") }}.
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



async def authenticate_connection(
    connection: HTTPConnection, session: AsyncSession
) -> Principal | None:
    """Authenticate an HTTP request or a WebSocket handshake with the enabled methods.

    The first authenticator that returns a Principal wins; None means no credential was
    sent. An authenticator that finds its credential but rejects it raises a 401 itself,
    which is deliberately not caught: an invalid credential never falls through to
    another method.
    """
    for authenticator in AUTHENTICATORS:
        principal = await authenticator.authenticate(connection, session)
        if principal is not None:
            return principal
    return None


__all__ = ["AUTHENTICATORS", "Authenticator", "Principal", "authenticate_connection"]
