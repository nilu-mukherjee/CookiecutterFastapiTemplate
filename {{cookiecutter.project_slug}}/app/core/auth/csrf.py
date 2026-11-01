"""CSRF protection for cookie-authenticated requests.

The token is ``"<nonce>.<mac>"`` with ``mac = HMAC-SHA256(secret_key,
"csrf:<user_id>:<nonce>")``. It is sent as a JS-readable cookie and must be
echoed in the ``X-CSRF-Token`` header on unsafe methods. The header must equal
the cookie (double submit) AND carry a valid MAC for the authenticated user, so
an attacker who can plant a cookie (e.g. from a sibling subdomain) still cannot
mint a token, and one user's token is useless for another user.

Only cookie methods call this; requests authenticated by an explicit header are exempt
because browsers never attach such credentials automatically.
"""
import base64
import hashlib
import hmac
import secrets
from uuid import UUID

from fastapi import Response
from starlette.requests import HTTPConnection

from app.core.auth.cookies import CSRF_COOKIE, set_cookie
from app.core.config import get_settings
from app.core.exceptions import AppError

CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_MAX_TOKEN_LENGTH = 256


def _mac(user_id: UUID, nonce: str) -> str:
    key = get_settings().secret_key.encode()
    digest = hmac.new(key, f"csrf:{user_id}:{nonce}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def issue_csrf_token(user_id: UUID) -> str:
    nonce = secrets.token_urlsafe(16)
    return f"{nonce}.{_mac(user_id, nonce)}"


def set_csrf_cookie(response: Response, user_id: UUID, *, max_age: int) -> str:
    """Issue a token for the user, set it as the (non-HttpOnly) cookie and return it."""
    token = issue_csrf_token(user_id)
    set_cookie(response, CSRF_COOKIE, token, max_age=max_age, httponly=False)
    return token


def _signed_for(token: str, user_id: UUID) -> bool:
    nonce, sep, mac = token.partition(".")
    if not sep or not nonce or not mac:
        return False
    return hmac.compare_digest(mac.encode(), _mac(user_id, nonce).encode())


def require_csrf(request: HTTPConnection, user_id: UUID) -> None:
    """Raise 403 ``csrf_failed`` unless an unsafe request carries a valid token."""
    # A WebSocket handshake has no method; its cookie credentials are guarded by the
    # Origin check instead (see app.api.v1.endpoints.ws).
    if request.scope.get("method", "GET") in SAFE_METHODS:
        return
    header = request.headers.get(CSRF_HEADER, "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    valid = (
        0 < len(header) <= _MAX_TOKEN_LENGTH
        and hmac.compare_digest(header.encode(), cookie.encode())
        and _signed_for(header, user_id)
    )
    if not valid:
        raise AppError(403, "csrf_failed", "CSRF token missing or invalid")
