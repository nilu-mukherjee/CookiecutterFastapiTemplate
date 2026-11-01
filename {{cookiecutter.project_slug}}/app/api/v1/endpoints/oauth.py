{%- set tenancy = cookiecutter.use_multitenancy == "y" -%}
{#- The credential issued on success: the first enabled of session, JWT cookie, Bearer. -#}
{%- set method = "session" if cookiecutter.use_auth_session == "y" else ("jwt_cookie" if cookiecutter.use_auth_jwt_cookie == "y" else "bearer") -%}
"""Social login (Google, GitHub): OAuth2 authorization code flow with PKCE.

On success the callback issues the same credential as a password login, through the
same code: {% if method == "session" %}a new server-side session (``session_id`` + ``csrf_token``
cookies), then a redirect to OAUTH_SUCCESS_REDIRECT_URL{% elif method == "jwt_cookie" %}JWT cookies (``access_token``, ``refresh_token``,
``csrf_token``), then a redirect to OAUTH_SUCCESS_REDIRECT_URL{% else %}a Bearer token pair as JSON (no cookies,
``Cache-Control: no-store``){% endif %}.
"""
import logging
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import {% if method == "bearer" %}JSONResponse, {% endif %}RedirectResponse
from fastapi.routing import APIRoute

from app.api.deps import (
{%- if method == "session" %}
    OAuthProvidersDep,
    SessionServiceDep,
    SocialLoginServiceDep,
{%- else %}
    AuthServiceDep,
    OAuthProvidersDep,
    SocialLoginServiceDep,
{%- endif %}
)
from app.api.v1.endpoints.auth import {% if method == "session" %}set_session_cookies{% elif method == "jwt_cookie" %}set_auth_cookies{% else %}token_pair{% endif %}
from app.core.auth.cookies import {% if method == "session" %}OAUTH_STATE_COOKIE, OAUTH_STATE_PATH, SESSION_COOKIE{% else %}OAUTH_STATE_COOKIE, OAUTH_STATE_PATH{% endif %}
from app.core.auth.oauth_providers import (
    OAuthProvider,
    OAuthProviders,
    ProviderError,
    ProviderName,
)
from app.core.auth.oauth_state import (
    OAuthState,
    clear_state_cookie,
    decode_state,
    new_state,
    set_state_cookie,
)
from app.core.config import get_settings
from app.core.exceptions import AppError
{%- if tenancy %}
from app.models.tenant import SLUG_PATTERN
{%- endif %}
from app.models.user import User

logger = logging.getLogger("app.oauth")



class StateClearingRoute(APIRoute):
    """The callback route: its response ALWAYS clears the single-use state cookie.

    The endpoint body cannot do this on its own: query validation (422) fails before it
    runs, and an unexpected error never reaches its return. So the whole route handler is
    wrapped, errors are turned into responses by the app's own exception handlers (same
    bodies and logging as everywhere else), and the clearing cookie is added to whatever
    comes out.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handle = super().get_route_handler()

        async def handle_and_clear(request: Request) -> Response:
            try:
                response = await handle(request)
            except Exception as exc:
                response = await _error_response(request, exc)
            clear_state_cookie(response)
            return response

        return handle_and_clear


async def _error_response(request: Request, exc: Exception) -> Response:
    """The response the app's exception handlers give for ``exc`` (most specific first)."""
    handlers: dict[Any, Any] = request.app.exception_handlers
    for cls in type(exc).__mro__:
        handler = handlers.get(cls)
        if handler is not None:
            response: Response = await handler(request, exc)
            return response
    raise exc


router = APIRouter(prefix="/oauth", tags=["oauth"])
callback_router = APIRouter(route_class=StateClearingRoute)
# Bounds for the provider's query parameters (real values are far shorter).
QueryValue = Annotated[str | None, Query(max_length=2048)]


def _provider(providers: OAuthProviders, name: str) -> tuple[ProviderName, OAuthProvider]:
    """The enabled provider of that name. Unknown and disabled providers are the same 404."""
    try:
        key = ProviderName(name)
    except ValueError:
        raise AppError(404, "not_found", "Not found") from None
    provider = providers.get(key)
    if provider is None:
        raise AppError(404, "not_found", "Not found")
    return key, provider


def _redirect_uri(provider: ProviderName) -> str:
    """The registered callback URL: from settings only, never from the request."""
    base = get_settings().oauth_redirect_base_url.rstrip("/")
    return f"{base}{OAUTH_STATE_PATH}/{provider.value}/callback"


@router.get("/{provider}/login", status_code=302, response_class=RedirectResponse)
async def oauth_login(
    provider: str,
    providers: OAuthProvidersDep,
{%- if tenancy %}
    # The tenant a NEW user would join (if it allows social signup); carried in the state.
    tenant: Annotated[str | None, Query(pattern=SLUG_PATTERN)] = None,
{%- endif %}
) -> RedirectResponse:
    """Start the login: redirect to the provider with a fresh state and PKCE challenge."""
    name, client = _provider(providers, provider)
    state = new_state(name{% if tenancy %}, tenant{% endif %})
    url = client.authorization_url(state.nonce, state.code_challenge, _redirect_uri(name))
    response = RedirectResponse(url, status_code=302)
    set_state_cookie(response, state)
    return response


def _invalid_state() -> AppError:
    return AppError(400, "invalid_oauth_state", "Invalid or expired login attempt")


def _verified_state(
    request: Request, provider: ProviderName, presented: str | None
) -> OAuthState:
    """The state from the signed cookie, if it is valid, unexpired, for this provider and
    matches the ``state`` the provider sent back."""
    raw = request.cookies.get(OAUTH_STATE_COOKIE)
    state = decode_state(raw) if raw else None
    if state is None or state.provider is not provider or not presented:
        raise _invalid_state()
    if not state.matches(presented):
        raise _invalid_state()
    return state


@callback_router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    providers: OAuthProvidersDep,
    social: SocialLoginServiceDep,
{%- if method == "session" %}
    sessions: SessionServiceDep,
{%- else %}
    auth: AuthServiceDep,
{%- endif %}
    code: QueryValue = None,
    state: QueryValue = None,
    error: QueryValue = None,
) -> Response:
    """The provider's redirect back: check the state, exchange the code, sign the user in.

    Whatever the outcome, the response clears the state cookie (StateClearingRoute).
    """
    name, client = _provider(providers, provider)
    carried = _verified_state(request, name, state)
    if error is not None or not code:
        # Denied at the provider (or a malformed redirect): no detail echoed back.
        raise AppError(400, "oauth_denied", "The login was not completed")
    try:
        profile = await client.fetch_profile(code, carried.code_verifier, _redirect_uri(name))
    except ProviderError as exc:
        logger.warning("social login provider failure: %s", exc)
        raise AppError(
            502, "oauth_provider_error", "The login provider could not complete the login"
        ) from exc
    user = await social.resolve(profile{% if tenancy %}, carried.tenant{% endif %})
{%- if method == "session" %}
    return await _start_session(request, sessions, user)
{%- else %}
    return await _issue_tokens(auth, user)
{%- endif %}
{%- if method == "session" %}


async def _start_session(request: Request, sessions: SessionServiceDep, user: User) -> Response:
    """A new session, exactly as a password login would (never adopting a presented id)."""
    issued = await sessions.start(user.id, request.cookies.get(SESSION_COOKIE))
    # A constant target from settings: never taken from the request (no open redirect).
    response = RedirectResponse(get_settings().oauth_success_redirect_url, status_code=302)
    set_session_cookies(response, issued)
    return response
{%- elif method == "jwt_cookie" %}


async def _issue_tokens(auth: AuthServiceDep, user: User) -> Response:
    """JWT cookies, exactly as a cookie-delivery password login would set them."""
    issued = await auth.login_user(user)
    # A constant target from settings: never taken from the request (no open redirect).
    response = RedirectResponse(get_settings().oauth_success_redirect_url, status_code=302)
    set_auth_cookies(response, issued)
    return response
{%- else %}


async def _issue_tokens(auth: AuthServiceDep, user: User) -> Response:
    """The token pair as JSON, the same body as a password login; never cached."""
    issued = await auth.login_user(user)
    return JSONResponse(token_pair(issued).model_dump(), headers={"Cache-Control": "no-store"})
{%- endif %}


# Added last: include_router copies the routes defined above (keeping their route class).
router.include_router(callback_router)
