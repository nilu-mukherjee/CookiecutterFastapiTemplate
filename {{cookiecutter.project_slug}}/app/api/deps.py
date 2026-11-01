{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set bearer = auth and cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set jwt_cookie = auth and cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set session = auth and cookiecutter.use_auth_session == "y" -%}
{%- set api_key = auth and cookiecutter.use_auth_api_key == "y" -%}
{%- set basic = auth and cookiecutter.use_auth_basic == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
{%- set tenancy = auth and cookiecutter.use_multitenancy == "y" -%}
{%- set use_jwt = bearer or jwt_cookie -%}
{%- set schemes = [] -%}
{%- if bearer %}{% set _ = schemes.extend(["HTTPAuthorizationCredentials", "HTTPBearer"]) %}{% endif -%}
{%- if jwt_cookie or session %}{% set _ = schemes.append("APIKeyCookie") %}{% endif -%}
{%- if api_key %}{% set _ = schemes.append("APIKeyHeader") %}{% endif -%}
{%- if basic %}{% set _ = schemes.extend(["HTTPBasic", "HTTPBasicCredentials"]) %}{% endif -%}
{% if rbac %}from collections.abc import Awaitable, Callable
{% endif %}from typing import Annotated
{% if tenancy %}from uuid import UUID
{% endif %}
{% if schemes %}from fastapi import Depends, Request, Security
{% set imported = schemes | sort | join(", ") %}{% if imported | length > 69 %}from fastapi.security import (
{%- for name in schemes | sort %}
    {{ name }},
{%- endfor %}
)
{% else %}from fastapi.security import {{ imported }}
{% endif %}{% elif auth %}from fastapi import Depends, Request
{% else %}from fastapi import Depends
{% endif %}{% if cookiecutter.use_redis == "y" %}from redis.asyncio import Redis
{% endif %}from sqlalchemy.ext.asyncio import AsyncSession
{% if basic %}from starlette.requests import HTTPConnection
{% endif %}
{% if cookiecutter.use_redis == "y" %}from app.cache.redis import get_redis
{% endif %}{% if auth %}from app.core.auth import AUTHENTICATORS, Principal
{% endif %}{% if api_key %}from app.core.auth.api_key import API_KEY_HEADER
{% endif %}{% if jwt_cookie or session %}from app.core.auth.cookies import {% if jwt_cookie %}ACCESS_COOKIE{% endif %}{% if jwt_cookie and session %}, {% endif %}{% if session %}SESSION_COOKIE{% endif %}
{% endif %}{% if auth %}from app.core.exceptions import AppError
{% endif %}from app.db.session import get_session
{% if api_key %}from app.services.api_key import ApiKeyService
{% endif %}{% if use_jwt %}from app.services.auth import AuthService
{% endif %}from app.services.item import ItemService
{% if rbac %}from app.services.rbac import RbacService
{% endif %}{% if session %}from app.services.session import SessionService
{% endif %}{% if tenancy %}from app.services.tenant import TenantService
{% endif %}{% if auth %}from app.services.user import UserService
{% endif %}
SessionDep = Annotated[AsyncSession, Depends(get_session)]
{%- if not tenancy %}


def get_item_service(session: SessionDep) -> ItemService:
    return ItemService(session)


ItemServiceDep = Annotated[ItemService, Depends(get_item_service)]
{%- endif %}
{%- if cookiecutter.use_redis == "y" %}

RedisDep = Annotated[Redis, Depends(get_redis)]
{%- endif %}
{%- if auth %}
{%- if not tenancy %}


def get_user_service(session: SessionDep) -> UserService:
    return UserService(session)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]
{%- endif %}
{%- if use_jwt %}


def get_auth_service(session: SessionDep) -> AuthService:
    return AuthService(session)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
{%- endif %}
{%- if api_key %}


def get_api_key_service(session: SessionDep) -> ApiKeyService:
    return ApiKeyService(session)


ApiKeyServiceDep = Annotated[ApiKeyService, Depends(get_api_key_service)]
{%- endif %}
{%- if rbac and not tenancy %}


def get_rbac_service(session: SessionDep) -> RbacService:
    return RbacService(session)


RbacServiceDep = Annotated[RbacService, Depends(get_rbac_service)]
{%- endif %}
{%- if session %}


def get_session_service(session: SessionDep) -> SessionService:
    return SessionService(session)


SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
{%- endif %}
{%- if bearer %}

# Only advertises the scheme in OpenAPI; JWTBearerAuthenticator does the checking.
bearer_scheme = HTTPBearer(auto_error=False)
{%- endif %}
{%- if api_key %}

# Only advertises the scheme in OpenAPI; ApiKeyAuthenticator does the checking.
api_key_scheme = APIKeyHeader(name=API_KEY_HEADER, auto_error=False, scheme_name="ApiKeyAuth")
{%- endif %}
{%- if session %}

# Only advertises the scheme in OpenAPI; SessionAuthenticator does the checking.
# Named explicitly: OpenAPI keys schemes by name, and the class name would collide
# with the JWT cookie scheme when both are enabled.
session_scheme = APIKeyCookie(name=SESSION_COOKIE, scheme_name="SessionCookie", auto_error=False)
{%- endif %}
{%- if jwt_cookie %}

# Only advertises the scheme in OpenAPI; JWTCookieAuthenticator does the checking.
cookie_scheme = APIKeyCookie(name=ACCESS_COOKIE, auto_error=False)
{%- endif %}
{%- if basic %}


class BasicScheme(HTTPBasic):
    """Only advertises the scheme in OpenAPI; BasicAuthenticator does the checking.

    FastAPI's own HTTPBasic raises 401 with ``WWW-Authenticate: Basic`` on a malformed
    header even with auto_error=False (and only accepts ASCII). That challenge would
    make browsers prompt for and cache credentials, so this one never parses anything.
    """

    async def __call__(self, request: HTTPConnection) -> None:
        return None


basic_scheme = BasicScheme(auto_error=False, scheme_name="BasicAuth")
{%- endif %}


async def get_current_principal(
    request: Request,
    session: SessionDep,
{%- if bearer %}
    _bearer: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
{%- endif %}
{%- if api_key %}
    _api_key: Annotated[str | None, Security(api_key_scheme)],
{%- endif %}
{%- if session %}
    _session: Annotated[str | None, Security(session_scheme)],
{%- endif %}
{%- if jwt_cookie %}
    _cookie: Annotated[str | None, Security(cookie_scheme)],
{%- endif %}
{%- if basic %}
    _basic: Annotated[HTTPBasicCredentials | None, Security(basic_scheme)],
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
{%- if tenancy %}


def get_tenant_id(principal: CurrentPrincipal) -> UUID:
    """The request's tenant: the authenticated user's tenant, from the database row.

    Never taken from a header, query string, body, path or token claim. Services and
    repositories receive it explicitly; nothing filters implicitly behind their back.
    """
    return principal.tenant_id


TenantId = Annotated[UUID, Depends(get_tenant_id)]


def get_item_service(session: SessionDep, tenant_id: TenantId) -> ItemService:
    return ItemService(session, tenant_id)


ItemServiceDep = Annotated[ItemService, Depends(get_item_service)]


def get_user_service(session: SessionDep, tenant_id: TenantId) -> UserService:
    return UserService(session, tenant_id)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]
{%- if rbac %}


def get_rbac_service(session: SessionDep, tenant_id: TenantId) -> RbacService:
    return RbacService(session, tenant_id)


RbacServiceDep = Annotated[RbacService, Depends(get_rbac_service)]
{%- endif %}


def get_tenant_service(session: SessionDep) -> TenantService:
    return TenantService(session)


TenantServiceDep = Annotated[TenantService, Depends(get_tenant_service)]
{%- endif %}
{%- if rbac %}


def require_permission(name: str) -> Callable[..., Awaitable[Principal]]:
    """Dependency factory: the request's principal must hold the permission.

    Permissions belong to the user, whatever authenticated the request (Bearer, cookie,
    session, API key, Basic): an API key has exactly its owner's permissions. They are read
    from the database on each request, so a role change applies to the next request; the
    result is memoised on ``request.state`` for that one request only. Superusers bypass.
    """

    async def dependency(
        request: Request, principal: CurrentPrincipal, rbac: RbacServiceDep
    ) -> Principal:
        if principal.is_superuser:
            return principal
        granted: set[str] | None = getattr(request.state, "permissions", None)
        if granted is None:
            granted = await rbac.permissions_for(principal.user_id)
            request.state.permissions = granted
        if name not in granted:
            raise AppError(403, "permission_denied", f"Missing required permission: {name}")
        return principal

    return dependency


UsersReader = Annotated[Principal, Depends(require_permission("users:read"))]
UsersWriter = Annotated[Principal, Depends(require_permission("users:write"))]
{%- endif %}
{%- if api_key %}


async def require_human(principal: CurrentPrincipal) -> Principal:
    """Refuse API-key requests: a key must not be able to mint or revoke API keys."""
    if principal.auth_method == "api_key":
        raise AppError(403, "api_key_not_allowed", "API keys cannot manage API keys")
    return principal


HumanPrincipal = Annotated[Principal, Depends(require_human)]
{%- endif %}
{%- endif %}
