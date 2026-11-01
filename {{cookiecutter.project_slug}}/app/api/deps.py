{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set bearer = auth and cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set jwt_cookie = auth and cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set use_jwt = bearer or jwt_cookie -%}
{%- set schemes = [] -%}
{%- if bearer %}{% set _ = schemes.extend(["HTTPAuthorizationCredentials", "HTTPBearer"]) %}{% endif -%}
{%- if jwt_cookie %}{% set _ = schemes.insert(0, "APIKeyCookie") %}{% endif -%}
from typing import Annotated

{% if schemes %}from fastapi import Depends, Request, Security
from fastapi.security import {{ schemes | join(", ") }}
{% elif auth %}from fastapi import Depends, Request
{% else %}from fastapi import Depends
{% endif %}{% if cookiecutter.use_redis == "y" %}from redis.asyncio import Redis
{% endif %}from sqlalchemy.ext.asyncio import AsyncSession

{% if cookiecutter.use_redis == "y" %}from app.cache.redis import get_redis
{% endif %}{% if auth %}from app.core.auth import AUTHENTICATORS, Principal
{% endif %}{% if jwt_cookie %}from app.core.auth.cookies import ACCESS_COOKIE
{% endif %}{% if auth %}from app.core.exceptions import AppError
{% endif %}from app.db.session import get_session
{% if use_jwt %}from app.services.auth import AuthService
{% endif %}from app.services.item import ItemService
{% if auth %}from app.services.user import UserService
{% endif %}
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_item_service(session: SessionDep) -> ItemService:
    return ItemService(session)


ItemServiceDep = Annotated[ItemService, Depends(get_item_service)]
{%- if cookiecutter.use_redis == "y" %}

RedisDep = Annotated[Redis, Depends(get_redis)]
{%- endif %}
{%- if auth %}


def get_user_service(session: SessionDep) -> UserService:
    return UserService(session)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]
{%- if use_jwt %}


def get_auth_service(session: SessionDep) -> AuthService:
    return AuthService(session)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
{%- endif %}
{%- if bearer %}

# Only advertises the scheme in OpenAPI; JWTBearerAuthenticator does the checking.
bearer_scheme = HTTPBearer(auto_error=False)
{%- endif %}
{%- if jwt_cookie %}

# Only advertises the scheme in OpenAPI; JWTCookieAuthenticator does the checking.
cookie_scheme = APIKeyCookie(name=ACCESS_COOKIE, auto_error=False)
{%- endif %}


async def get_current_principal(
    request: Request,
    session: SessionDep,
{%- if bearer %}
    _bearer: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
{%- endif %}
{%- if jwt_cookie %}
    _cookie: Annotated[str | None, Security(cookie_scheme)],
{%- endif %}
) -> Principal:
    """Authenticate the request with the enabled methods, in order.

    The first authenticator that returns a Principal wins. An authenticator that
    finds its credential but rejects it raises a 401 itself, which is deliberately
    not caught: an invalid credential never falls through to another method.
    """
    for authenticator in AUTHENTICATORS:
        principal = await authenticator.authenticate(request, session)
        if principal is not None:
            return principal
    raise AppError(
        401,
        "not_authenticated",
        "Not authenticated",
{%- if bearer %}
        headers={"WWW-Authenticate": "Bearer"},
{%- endif %}
    )


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


async def require_superuser(principal: CurrentPrincipal) -> Principal:
    if not principal.is_superuser:
        raise AppError(403, "forbidden", "Superuser privileges required")
    return principal


SuperuserPrincipal = Annotated[Principal, Depends(require_superuser)]
{%- endif %}
