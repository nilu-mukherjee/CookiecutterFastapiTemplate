"""Names and attributes of every auth cookie, in one place.

Secure/SameSite/Domain come from settings; Domain is only sent when configured,
so cookies are host-only by default. Clearing a cookie repeats the same
Path/Domain, otherwise the browser would keep the original.
"""
from fastapi import Response

from app.core.config import get_settings

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
# Readable by JavaScript on purpose: the client echoes it in X-CSRF-Token.
CSRF_COOKIE = "csrf_token"

# The refresh token is only ever sent to the auth endpoints.
REFRESH_COOKIE_PATH = "/api/v1/auth"


def set_cookie(
    response: Response,
    name: str,
    value: str,
    *,
    max_age: int,
    path: str = "/",
    httponly: bool = True,
) -> None:
    settings = get_settings()
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path=path,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=httponly,
        samesite=settings.cookie_samesite,
    )


def clear_cookie(
    response: Response, name: str, *, path: str = "/", httponly: bool = True
) -> None:
    settings = get_settings()
    response.delete_cookie(
        name,
        path=path,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=httponly,
        samesite=settings.cookie_samesite,
    )
