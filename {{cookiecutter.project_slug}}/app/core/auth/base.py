{%- set tenancy = cookiecutter.use_multitenancy == "y" -%}
{%- set methods = [] -%}
{%- if cookiecutter.use_auth_jwt_bearer == "y" %}{% set _ = methods.append("bearer") %}{% endif -%}
{%- if cookiecutter.use_auth_api_key == "y" %}{% set _ = methods.append("api_key") %}{% endif -%}
{%- if cookiecutter.use_auth_session == "y" %}{% set _ = methods.append("session") %}{% endif -%}
{%- if cookiecutter.use_auth_jwt_cookie == "y" %}{% set _ = methods.append("jwt_cookie") %}{% endif -%}
{%- if cookiecutter.use_auth_basic == "y" %}{% set _ = methods.append("basic") %}{% endif -%}
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

# The enabled methods only.
AuthMethod = Literal[{% for m in methods %}"{{ m }}"{% if not loop.last %}, {% endif %}{% endfor %}]


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is making the request, whatever method authenticated them."""

    user_id: UUID
{%- if tenancy %}
    # The user's tenant, read from the user's database row on every request: the only
    # source of the request's tenant (never a header, query, body or token claim).
    tenant_id: UUID
{%- endif %}
    is_superuser: bool
    # How the request authenticated; some actions refuse some methods
    # (an API key must not be able to mint more API keys).
    auth_method: AuthMethod


class Authenticator(Protocol):
    """One authentication method (Bearer, API key, session cookie, ...).

    ``authenticate`` returns None when the request carries no credential of this
    kind, a Principal when it carries a valid one, and raises
    ``AppError(401, "invalid_credentials", ...)`` when it carries an invalid one.
    Raising (rather than returning None) stops the next method from being tried.
    """

    async def authenticate(
        self, request: HTTPConnection, session: AsyncSession
    ) -> Principal | None:
        ...
