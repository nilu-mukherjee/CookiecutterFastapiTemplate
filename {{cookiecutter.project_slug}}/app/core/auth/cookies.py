{%- set jwt_cookie = cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set session = cookiecutter.use_auth_session == "y" -%}
{%- set social = cookiecutter.use_social_login == "y" -%}
"""Names and attributes of every auth cookie, in one place.

Secure/SameSite/Domain come from settings; Domain is only sent when configured,
so cookies are host-only by default. Clearing a cookie repeats the same
Path/Domain, otherwise the browser would keep the original.
"""
from typing import Literal

from fastapi import Response

from app.core.config import get_settings

{% if jwt_cookie %}ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
{% endif %}{% if session %}# Opaque server-side session id; only its hash is stored.
SESSION_COOKIE = "session_id"
{% endif %}{% if jwt_cookie or session %}# Readable by JavaScript on purpose: the client echoes it in X-CSRF-Token.
CSRF_COOKIE = "csrf_token"
{% endif %}{% if social %}# Signed, short-lived OAuth state (nonce, PKCE verifier) between login and callback;
# only ever sent to the OAuth endpoints.
OAUTH_STATE_COOKIE = "oauth_state"
OAUTH_STATE_PATH = "/api/v1/oauth"
{% endif %}{% if jwt_cookie %}
# The refresh token is only ever sent to the auth endpoints.
REFRESH_COOKIE_PATH = "/api/v1/auth"
{% endif %}
SameSite = Literal["lax", "strict", "none"]


def set_cookie(
    response: Response,
    name: str,
    value: str,
    *,
    max_age: int,
    path: str = "/",
    httponly: bool = True,
    samesite: SameSite | None = None,
) -> None:
    """``samesite`` overrides the configured COOKIE_SAMESITE for this one cookie."""
    settings = get_settings()
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path=path,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=httponly,
        samesite=samesite or settings.cookie_samesite,
    )


def clear_cookie(
    response: Response,
    name: str,
    *,
    path: str = "/",
    httponly: bool = True,
    samesite: SameSite | None = None,
) -> None:
    settings = get_settings()
    response.delete_cookie(
        name,
        path=path,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=httponly,
        samesite=samesite or settings.cookie_samesite,
    )
