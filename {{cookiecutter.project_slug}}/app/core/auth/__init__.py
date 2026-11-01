{%- set bearer = cookiecutter.use_auth_jwt_bearer == "y" -%}
"""Assembles the enabled authentication methods, in precedence order."""
from app.core.auth.base import Authenticator, Principal
{%- if bearer %}
from app.core.auth.jwt_bearer import JWTBearerAuthenticator
{%- endif %}

# The first authenticator that finds its credential decides (no fall-through).
# Order: Bearer, API key, session cookie, JWT cookie, Basic.
AUTHENTICATORS: list[Authenticator] = [
{%- if bearer %}
    JWTBearerAuthenticator(),
{%- endif %}
]

__all__ = ["AUTHENTICATORS", "Authenticator", "Principal"]
