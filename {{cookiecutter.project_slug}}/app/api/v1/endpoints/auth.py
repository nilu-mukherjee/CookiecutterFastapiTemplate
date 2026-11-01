{%- set bearer = cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set jwt_cookie = cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set both = bearer and jwt_cookie -%}
{%- if both -%}
{%- set issued_model = "TokenPair | CookieSession" -%}
{%- elif bearer -%}
{%- set issued_model = "TokenPair" -%}
{%- else -%}
{%- set issued_model = "CookieSession" -%}
{%- endif -%}
{% if jwt_cookie %}from fastapi import APIRouter, Request, Response
{% else %}from fastapi import APIRouter, Response
{% endif %}
from app.api.deps import AuthServiceDep
{%- if jwt_cookie %}
from app.core.auth.cookies import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    REFRESH_COOKIE_PATH,
    clear_cookie,
    set_cookie,
)
from app.core.auth.csrf import require_csrf, set_csrf_cookie
from app.core.config import get_settings
from app.core.exceptions import AppError
{%- endif %}
{%- if both %}
from app.schemas.v1.auth import CookieSession, LoginRequest, RefreshRequest, TokenPair
{%- elif bearer %}
from app.schemas.v1.auth import LoginRequest, RefreshRequest, TokenPair
{%- else %}
from app.schemas.v1.auth import CookieSession, LoginRequest
{%- endif %}
from app.services.auth import {% if jwt_cookie %}AuthService, {% endif %}IssuedTokens

router = APIRouter(prefix="/auth", tags=["auth"])
{%- if bearer %}


def _pair(issued: IssuedTokens) -> TokenPair:
    return TokenPair(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=issued.expires_in,
    )
{%- endif %}
{%- if jwt_cookie %}


def _set_auth_cookies(response: Response, issued: IssuedTokens) -> CookieSession:
    """Deliver the tokens as cookies; the body only carries the CSRF token."""
    refresh_max_age = get_settings().refresh_token_expire_days * 24 * 60 * 60
    set_cookie(response, ACCESS_COOKIE, issued.access_token, max_age=issued.expires_in)
    set_cookie(
        response,
        REFRESH_COOKIE,
        issued.refresh_token,
        max_age=refresh_max_age,
        path=REFRESH_COOKIE_PATH,
    )
    csrf_token = set_csrf_cookie(response, issued.user_id, max_age=refresh_max_age)
    return CookieSession(expires_in=issued.expires_in, csrf_token=csrf_token)


def _clear_auth_cookies(response: Response) -> None:
    clear_cookie(response, ACCESS_COOKIE)
    clear_cookie(response, REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)
    clear_cookie(response, CSRF_COOKIE, httponly=False)


async def _refresh_cookie_owner(
    request: Request, service: AuthService
) -> tuple[str, bool]:
    """Return the refresh cookie and whether it was ever issued (CSRF-checked if so).

    The owner of the refresh token is the user the CSRF token must be bound to.
    """
    raw = request.cookies.get(REFRESH_COOKIE, "")
    owner = await service.refresh_token_owner(raw) if raw else None
    if owner is None:
        return raw, False
    require_csrf(request, owner)
    return raw, True
{%- endif %}

{%- if not jwt_cookie %}


# POST only: credentials never go in a query string, and GET must not log anyone in.
@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest, service: AuthServiceDep) -> TokenPair:
    return _pair(await service.login(body.email, body.password))


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, service: AuthServiceDep) -> TokenPair:
    return _pair(await service.refresh(body.refresh_token))


@router.post("/logout", status_code=204)
async def logout(body: RefreshRequest, service: AuthServiceDep) -> Response:
    await service.logout(body.refresh_token)
    return Response(status_code=204)
{%- else %}


# POST only: credentials never go in a query string, and GET must not log anyone in.
@router.post("/login", response_model={{ issued_model }})
async def login(
    body: LoginRequest, service: AuthServiceDep, response: Response
) -> {{ issued_model }}:
    issued = await service.login(body.email, body.password)
{%- if both %}
    if body.delivery == "cookie":
        return _set_auth_cookies(response, issued)
    return _pair(issued)
{%- else %}
    return _set_auth_cookies(response, issued)
{%- endif %}


@router.post("/refresh", response_model={{ issued_model }})
async def refresh(
    request: Request,
    response: Response,
    service: AuthServiceDep,
{%- if both %}
    body: RefreshRequest | None = None,
{%- endif %}
) -> {{ issued_model }}:
{%- if both %}
    if body is not None:  # token in the body: Bearer flow, no cookies involved
        return _pair(await service.refresh(body.refresh_token))
{%- endif %}
    raw, known = await _refresh_cookie_owner(request, service)
    if not raw:
        raise AppError(401, "not_authenticated", "No refresh token")
    if not known:
        raise AppError(401, "invalid_credentials", "Invalid or expired refresh token")
    return _set_auth_cookies(response, await service.refresh(raw))


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    service: AuthServiceDep,
{%- if both %}
    body: RefreshRequest | None = None,
{%- endif %}
) -> Response:
{%- if both %}
    if body is not None:
        await service.logout(body.refresh_token)
        return Response(status_code=204)
{%- endif %}
    # Idempotent: without a known refresh cookie there is nothing to revoke,
    # but the cookies are still cleared.
    raw, known = await _refresh_cookie_owner(request, service)
    if known:
        await service.logout(raw)
    response = Response(status_code=204)
    _clear_auth_cookies(response)
    return response
{%- endif %}
