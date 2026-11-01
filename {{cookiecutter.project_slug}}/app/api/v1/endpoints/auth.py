{%- set bearer = cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set jwt_cookie = cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set session = cookiecutter.use_auth_session == "y" -%}
{%- set jwt = bearer or jwt_cookie -%}
{%- set both = bearer and jwt_cookie -%}
{#- Plain refresh-token endpoints, no cookies and no sessions. -#}
{%- set bearer_only = bearer and not jwt_cookie and not session -%}
{%- set cookie_any = jwt_cookie or session -%}
{%- set models = [] -%}
{%- if bearer %}{% set _ = models.append("TokenPair") %}{% endif -%}
{%- if jwt_cookie %}{% set _ = models.append("CookieSession") %}{% endif -%}
{%- if session %}{% set _ = models.append("SessionLogin") %}{% endif -%}
{%- set issued_model = models | join(" | ") -%}
{%- set schemas = ["LoginRequest"] + models -%}
{%- if bearer %}{% set _ = schemas.append("RefreshRequest") %}{% endif -%}
{% if cookie_any %}from fastapi import APIRouter, Request, Response
{% else %}from fastapi import APIRouter, Response
{% endif %}
from app.api.deps import {% if jwt %}AuthServiceDep{% endif %}{% if jwt and session %}, {% endif %}{% if session %}SessionServiceDep{% endif %}
{%- if cookie_any %}
from app.core.auth.cookies import (
{%- if jwt_cookie %}
    ACCESS_COOKIE,
{%- endif %}
    CSRF_COOKIE,
{%- if jwt_cookie %}
    REFRESH_COOKIE,
    REFRESH_COOKIE_PATH,
{%- endif %}
{%- if session %}
    SESSION_COOKIE,
{%- endif %}
    clear_cookie,
    set_cookie,
)
from app.core.auth.csrf import require_csrf, set_csrf_cookie
{%- endif %}
{%- if jwt_cookie %}
from app.core.config import get_settings
{%- endif %}
{%- if jwt_cookie %}
from app.core.exceptions import AppError
{%- endif %}
from app.schemas.v1.auth import {{ schemas | sort | join(", ") }}
{%- if jwt_cookie %}
from app.services.auth import AuthService, IssuedTokens
{%- elif jwt %}
from app.services.auth import IssuedTokens
{%- endif %}
{%- if session %}
from app.services.session import IssuedSession
{%- endif %}

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
{%- if session %}


def _set_session_cookies(response: Response, issued: IssuedSession) -> SessionLogin:
    """Deliver the session id as an HttpOnly cookie; the body only carries the CSRF token."""
    set_cookie(response, SESSION_COOKIE, issued.raw_id, max_age=issued.expires_in)
    csrf_token = set_csrf_cookie(response, issued.user_id, max_age=issued.expires_in)
    return SessionLogin(expires_in=issued.expires_in, csrf_token=csrf_token)


def _clear_session_cookies(response: Response) -> None:
    clear_cookie(response, SESSION_COOKIE)
    clear_cookie(response, CSRF_COOKIE, httponly=False)
{%- endif %}

{%- if bearer_only %}


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
    body: LoginRequest,
{%- if session %}
    request: Request,
{%- endif %}
    response: Response,
{%- if jwt %}
    service: AuthServiceDep,
{%- endif %}
{%- if session %}
    sessions: SessionServiceDep,
{%- endif %}
) -> {{ issued_model }}:
{%- if session %}
{%- if jwt %}
    if body.delivery == "session":
{%- set pad = "    " %}
{%- else %}
{%- set pad = "" %}
{%- endif %}
    {{ pad }}# Never adopts a presented session id; a valid one is deleted (fixation).
    {{ pad }}presented = request.cookies.get(SESSION_COOKIE)
    {{ pad }}started = await sessions.login(body.email, body.password, presented)
    {{ pad }}return _set_session_cookies(response, started)
{%- endif %}
{%- if jwt %}
    issued = await service.login(body.email, body.password)
{%- if both %}
    if body.delivery == "cookie":
        return _set_auth_cookies(response, issued)
    return _pair(issued)
{%- elif bearer %}
    return _pair(issued)
{%- else %}
    return _set_auth_cookies(response, issued)
{%- endif %}
{%- endif %}
{%- if jwt %}


@router.post("/refresh", response_model={{ "TokenPair | CookieSession" if both else ("TokenPair" if bearer else "CookieSession") }})
async def refresh(
{%- if jwt_cookie %}
    request: Request,
    response: Response,
{%- endif %}
    service: AuthServiceDep,
{%- if both %}
    body: RefreshRequest | None = None,
{%- elif bearer %}
    body: RefreshRequest,
{%- endif %}
) -> {{ "TokenPair | CookieSession" if both else ("TokenPair" if bearer else "CookieSession") }}:
{%- if both %}
    if body is not None:  # token in the body: Bearer flow, no cookies involved
        return _pair(await service.refresh(body.refresh_token))
{%- elif bearer %}
    return _pair(await service.refresh(body.refresh_token))
{%- endif %}
{%- if jwt_cookie %}
    raw, known = await _refresh_cookie_owner(request, service)
    if not raw:
        raise AppError(401, "not_authenticated", "No refresh token")
    if not known:
        raise AppError(401, "invalid_credentials", "Invalid or expired refresh token")
    return _set_auth_cookies(response, await service.refresh(raw))
{%- endif %}
{%- endif %}


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
{%- if jwt %}
    service: AuthServiceDep,
{%- endif %}
{%- if session %}
    sessions: SessionServiceDep,
{%- endif %}
{%- if bearer %}
    body: RefreshRequest | None = None,
{%- endif %}
) -> Response:
{%- if bearer %}
    if body is not None:
        await service.logout(body.refresh_token)
        return Response(status_code=204)
{%- endif %}
{%- if session %}
    # Idempotent: an unknown or expired session is already logged out, cookies are cleared.
    raw_session = request.cookies.get(SESSION_COOKIE)
    if raw_session:
        row = await sessions.resolve(raw_session)
        if row is not None:
            require_csrf(request, row.user_id)
            await sessions.revoke(row)
        response = Response(status_code=204)
        _clear_session_cookies(response)
        return response
{%- endif %}
{%- if jwt_cookie %}
    # Idempotent: without a known refresh cookie there is nothing to revoke,
    # but the cookies are still cleared.
    raw, known = await _refresh_cookie_owner(request, service)
    if known:
        await service.logout(raw)
    response = Response(status_code=204)
    _clear_auth_cookies(response)
    return response
{%- else %}
    response = Response(status_code=204)
    _clear_session_cookies(response)
    return response
{%- endif %}
{%- endif %}
